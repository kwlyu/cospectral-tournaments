import os
import re
import time
import hashlib
import threading
from collections import defaultdict
from datetime import datetime
from sage.all import *
import json
import math # For math.ceil
import sys # For sys.exit

# Import constants and functions from readme_generator.py
# This ensures that main_runner uses the same definitions for directories,
# non-iso tournament counts, and the data collection logic.
from readme_gen import (
    ROOT_OUTPUT_BASE_DIR,
    GENERATION_CHECKPOINT_FILE_NAME,
    CHECKING_CHECKPOINT_FILE_NAME,
    NON_ISO_TOURNAMENTS,
    collect_all_data_for_readme,
    update_readme_main,
    FILE_EXTENSION, # Added for consistency in file naming
    PRE_EXISTING_RESULTS_DIR # Imported for consistency, though not directly used for computation here
)

# ===================== Environment Variables =====================
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

# ===================== Global Constants and Paths =====================
# File Size Limit for output .txt files (10 MB)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024 

# Checkpointing constants (re-defined here for clarity, but match readme_generator)
CHECKPOINT_INTERVAL = 100000 

# ===================== Data Storage for Real-time Progress (Internal to this script) =====================
# These are now only for internal logging/display within this runner script, not for README generation.
overall_results_summary_internal = {} 
overall_results_lock_internal = threading.Lock() 
currently_processing_n_internal = None

# Global variable to store char poly from Phase 1 checkpoint for easy access in Phase 2
char_poly_lookup = {} 

# ===================== Control Flag for Rewriting =====================
# Set this to True to rewrite results for orders 1-10 with counterexamples.
# Set to False after the initial rewrite to resume normal operation.
REWRITE_COMPLETED_ORDERS = False # Changed to False after initial rewrite
REWRITE_TARGET_ORDERS = range(1, 11) # Orders to rewrite (1 to 10 inclusive)

# ===================== Core Computation Utilities =====================
def seidel_matrix(T):
    """Computes the Seidel matrix of a given tournament T."""
    A = T.adjacency_matrix()
    return A - A.transpose()

def _create_mckay_graph_from_seidel(S_matrix):
    """
    Constructs the McKay graph (as a DiGraph) from a Seidel matrix.
    Args:
        S_matrix: The Seidel matrix of a tournament.
    Returns:
        A Sage DiGraph representing the McKay graph.
    """
    n = S_matrix.nrows()
    D = zero_matrix(2 * n)
    for i in range(n):
        for j in range(n):
            if S_matrix[i, j] == 1: # Edge i->j in tournament implies 1 in Seidel matrix
                D[2*i, 2*j] = 1
                D[2*i+1, 2*j+1] = 1
            elif S_matrix[i, j] == -1: # Edge j->i in tournament implies -1 in Seidel matrix
                D[2*i, 2*j+1] = 1
                D[2*i+1, 2*j] = 1
    return DiGraph(D)

def mckay_check(T1, T2):
    """
    Checks if two tournaments T1 and T2 are switching equivalent using McKay's criterion.
    They are switching equivalent if and only if their McKay graphs are isomorphic.
    Args:
        T1: The first tournament (Sage DiGraph object).
        T2: The second tournament (Sage DiGraph object).
    Returns:
        True if T1 and T2 are switching equivalent, False otherwise.
    """
    S1 = seidel_matrix(T1)
    S2 = seidel_matrix(T2)
    
    G_mckay_1 = _create_mckay_graph_from_seidel(S1)
    G_mckay_2 = _create_mckay_graph_from_seidel(S2)
    
    return G_mckay_1.is_isomorphic(G_mckay_2)

def hash_charpoly(poly):
    return hashlib.md5(str(poly).encode()).hexdigest()[:8]

# ===================== File Path Helpers for Main Computation =====================
def get_computation_paths(n, h=None):
    base_dir_for_n = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n}") 
    return {
        "n_dir": base_dir_for_n, 
        "class_dir": os.path.join(base_dir_for_n, "classes"),
        "class_file": os.path.join(base_dir_for_n, "classes", f"class_{h}.txt") if h else None,
    }

def find_latest_part_file(n, output_dir_override=None):
    """
    Finds the latest part file for the main output (tournaments_n_X_partY.txt)
    for a given n. Can specify an override directory if needed.
    """
    output_dir = output_dir_override if output_dir_override else os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n}")
    if not os.path.exists(output_dir):
        return 0, None

    max_part = 0
    latest_filepath = None
    
    # Check for base file (tournaments_n_X.txt) first
    base_file = os.path.join(output_dir, f"tournaments_n_{n}{FILE_EXTENSION}")
    if os.path.exists(base_file):
        max_part = 0 # Default part for the base file
        latest_filepath = base_file

    # Check for part files (tournaments_n_X_partY.txt)
    for filename in os.listdir(output_dir):
        match = re.match(rf'tournaments_n_{n}_part(\d+){re.escape(FILE_EXTENSION)}$', filename)
        if match:
            part_num = int(match.group(1))
            if part_num >= max_part: # Use >= to pick latest part if base file exists
                max_part = part_num
                latest_filepath = os.path.join(output_dir, filename)
    
    return max_part, latest_filepath

# ===================== Phase 1: Generation and Classification =====================
def run_sequential(n):
    global currently_processing_n_internal
    
    n_dir_path = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n}")
    os.makedirs(n_dir_path, exist_ok=True) 
    checkpoint_path = os.path.join(n_dir_path, GENERATION_CHECKPOINT_FILE_NAME)

    start_total_tournaments_generated = 0
    initial_class_data_for_current_n = defaultdict(lambda: {"count": 0, "characteristic_polynomial": ""})
    initial_generated_charpoly_hashes = set()
    gen_phase_start_time_epoch = time.time() # Default start time for current run
    
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, 'r') as f:
                checkpoint_data = json.load(f)
            start_total_tournaments_generated = checkpoint_data.get("total_tournaments_generated", 0)
            
            loaded_class_data = checkpoint_data.get("class_data_for_current_n", {})
            for h_key, data in loaded_class_data.items():
                split_key = h_key.split('_', 1) 
                current_n_val = int(split_key[0])
                current_h_val = split_key[1]
                initial_class_data_for_current_n[(current_n_val, current_h_val)] = data
                initial_generated_charpoly_hashes.add(current_h_val)

            # Preserve original start time if resuming, otherwise use current time
            if "start_time_epoch" in checkpoint_data:
                gen_phase_start_time_epoch = checkpoint_data["start_time_epoch"]
            else:
                 # If checkpoint exists but no start_time_epoch, treat as new start
                print(f"Warning: Old checkpoint format detected for n={n}. Resetting generation start time.")
                gen_phase_start_time_epoch = time.time()
                
            print(f"[RESUME] Resuming n={n} from checkpoint. Already processed {start_total_tournaments_generated} tournaments.")
        except json.JSONDecodeError as e:
            print(f"Warning: Could not decode checkpoint file {checkpoint_path}: {e}. Starting from scratch for n={n}.")
            start_total_tournaments_generated = 0
            initial_class_data_for_current_n = defaultdict(lambda: {"count": 0, "characteristic_polynomial": ""})
            initial_generated_charpoly_hashes = set()
            gen_phase_start_time_epoch = time.time() # New start time if corrupted

    total_tournaments_generated = start_total_tournaments_generated
    class_data_for_current_n = initial_class_data_for_current_n
    generated_charpoly_hashes = initial_generated_charpoly_hashes

    with overall_results_lock_internal:
        currently_processing_n_internal = n
        overall_results_summary_internal[n] = {
            'completed': False,
            'status': 'In Progress (Generation)',
            'yes_classes': 0, 'no_classes': 0, 'total_classes': len(generated_charpoly_hashes),
            'current_progress_generated_tournaments': total_tournaments_generated,
            'current_progress_checked_classes': 0,
            'current_status_message': f"Generating tournaments for n={n}...",
            'generation_start_time_epoch': gen_phase_start_time_epoch # Store the actual start time
        }

    print(f"[GEN/CHK] Starting processing for n={n}")
    
    paths_n = get_computation_paths(n)
    os.makedirs(paths_n["class_dir"], exist_ok=True)

    tournaments_gen = digraphs.tournaments_nauty(n)
    
    # Save checkpoint at the very beginning to establish start_time_epoch
    try:
        with open(checkpoint_path, 'w') as f:
            serializable_class_data = {f"{k[0]}_{k[1]}": v for k, v in class_data_for_current_n.items()}
            json.dump({
                "total_tournaments_generated": total_tournaments_generated,
                "class_data_for_current_n": serializable_class_data,
                "start_time_epoch": gen_phase_start_time_epoch, # Save initial start time
                "last_update_time_epoch": time.time() # And current update time
            }, f)
    except Exception as e:
        print(f"Error saving initial checkpoint for n={n}: {e}")

    for i, T in enumerate(tournaments_gen): 
        if i < start_total_tournaments_generated:
            if i % CHECKPOINT_INTERVAL == 0: 
                print(f"[SKIP] Skipping tournament {i}/{start_total_tournaments_generated} for n={n}...")
            continue 

        seidel_mat = seidel_matrix(T)
        poly = seidel_mat.charpoly()
        h = hash_charpoly(poly)
        
        class_info = class_data_for_current_n[(n, h)]
        class_info["count"] += 1 
        class_info["characteristic_polynomial"] = str(poly)
        generated_charpoly_hashes.add(h) 

        # Write adjacency matrix without outer brackets for proper re-reading
        matrix_str_lines = []
        for row_idx in range(T.order()):
            matrix_str_lines.append(" ".join(map(str, T.adjacency_matrix().row(row_idx))))
        with open(get_computation_paths(n, h=h)["class_file"], "a") as f:
            f.write("\n".join(matrix_str_lines) + "\n\n")
        
        total_tournaments_generated += 1 

        if total_tournaments_generated % CHECKPOINT_INTERVAL == 0:
            try:
                with open(checkpoint_path, 'w') as f:
                    serializable_class_data = {f"{k[0]}_{k[1]}": v for k, v in class_data_for_current_n.items()}
                    json.dump({
                        "total_tournaments_generated": total_tournaments_generated,
                        "class_data_for_current_n": serializable_class_data,
                        "start_time_epoch": gen_phase_start_time_epoch, # Persist original start time
                        "last_update_time_epoch": time.time() # Update last update time
                    }, f)
                print(f"[CHECKPOINT] Saved checkpoint at {total_tournaments_generated} tournaments for n={n}.")
            except Exception as e:
                print(f"Error saving checkpoint for n={n}: {e}")

        with overall_results_lock_internal:
            overall_results_summary_internal[n]['current_progress_generated_tournaments'] = total_tournaments_generated
            overall_results_summary_internal[n]['total_classes'] = len(generated_charpoly_hashes) 
            expected_total_tournaments = NON_ISO_TOURNAMENTS.get(n, 0)
            if expected_total_tournaments == 0:
                overall_results_summary_internal[n]['current_status_message'] = f"Generated {total_tournaments_generated} tournaments for n={n} (total known: N/A)."
            else:
                gen_percent = (total_tournaments_generated / expected_total_tournaments) * 100
                overall_results_summary_internal[n]['current_status_message'] = f"Generated {total_tournaments_generated}/{expected_total_tournaments} tournaments for n={n} ({gen_percent:.2f}%)."
            
    print(f"[GEN/CHK] Done generating for n={n}. Stored {total_tournaments_generated} tournaments into {len(generated_charpoly_hashes)} classes.")
    
    # Save final generation checkpoint
    try:
        with open(checkpoint_path, 'w') as f:
            serializable_class_data = {f"{k[0]}_{k[1]}": v for k, v in class_data_for_current_n.items()}
            json.dump({
                "total_tournaments_generated": total_tournaments_generated,
                "class_data_for_current_n": serializable_class_data,
                "start_time_epoch": gen_phase_start_time_epoch, # Persist original start time
                "last_update_time_epoch": time.time() # Final update time
            }, f)
        print(f"[CHECKPOINT] Saved final checkpoint at {total_tournaments_generated} tournaments for n={n}.")
    except Exception as e:
        print(f"Error saving final checkpoint for n={n}: {e}")

    with overall_results_lock_internal:
        overall_results_summary_internal[n]['current_status_message'] = f"Generation complete for n={n}. Switching equivalence check needs to be run separately."
        overall_results_summary_internal[n]['completed'] = True
        overall_results_summary_internal[n]['status'] = "GENERATION_ONLY" # Mark as generation only complete for next phase
        overall_results_summary_internal[n]['yes_classes'] = 0 # Reset for checking phase
        overall_results_summary_internal[n]['no_classes'] = 0 # Reset for checking phase

    with overall_results_lock_internal:
        if currently_processing_n_internal == n:
            currently_processing_n_internal = None

# ===================== Phase 2: Checking for Switching Equivalence =====================

# Helper to read adjacency matrices from a file
def read_adj_matrices_from_file(filepath):
    """
    Generator that yields each adjacency matrix string from a class_H.txt file.
    Matrices are separated by double newlines.
    """
    current_matrix_lines = []
    with open(filepath, 'r') as f:
        for line in f:
            stripped_line = line.strip()
            if stripped_line == "": 
                if current_matrix_lines:
                    yield "\n".join(current_matrix_lines)
                    current_matrix_lines = []
            else:
                current_matrix_lines.append(stripped_line)
        if current_matrix_lines: 
            yield "\n".join(current_matrix_lines)

def run_checking_phase(n):
    global currently_processing_n_internal, char_poly_lookup
    
    n_dir_path = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n}")
    checking_checkpoint_path = os.path.join(n_dir_path, CHECKING_CHECKPOINT_FILE_NAME)

    # Initialize checking state
    start_total_checked_classes = 0
    start_total_yes_classes = 0
    start_total_no_classes = 0
    last_processed_class_filename = None
    chk_phase_start_time_epoch = time.time() # Default start time for current run

    # Attempt to load checking checkpoint
    if os.path.exists(checking_checkpoint_path):
        try:
            with open(checking_checkpoint_path, 'r') as f:
                chkpt_data = json.load(f)
            last_processed_class_filename = chkpt_data.get("last_processed_class_filename")
            start_total_checked_classes = chkpt_data.get("total_checked_classes", 0)
            start_total_yes_classes = chkpt_data.get("total_yes_classes", 0)
            start_total_no_classes = chkpt_data.get("total_no_classes", 0)

            # Preserve original start time if resuming
            if "start_time_epoch" in chkpt_data:
                chk_phase_start_time_epoch = chkpt_data["start_time_epoch"]
            else:
                print(f"Warning: Old checking checkpoint format detected for n={n}. Resetting checking start time.")
                chk_phase_start_time_epoch = time.time()
            
            if last_processed_class_filename == "COMPLETE":
                print(f"[CHECK RESUME] Checking for n={n} already completed in a previous run. Skipping.")
                with overall_results_lock_internal:
                    overall_results_summary_internal[n]['completed'] = True
                    overall_results_summary_internal[n]['status'] = "CHECKING_COMPLETE"
                    overall_results_summary_internal[n]['current_progress_checked_classes'] = start_total_checked_classes
                    overall_results_summary_internal[n]['yes_classes'] = start_total_yes_classes
                    overall_results_summary_internal[n]['no_classes'] = start_total_no_classes
                    overall_results_summary_internal[n]['current_status_message'] = ""
                return # Exit if already completed
            else:
                print(f"[CHECK RESUME] Resuming checking for n={n} from class '{last_processed_class_filename}'. Already processed {start_total_checked_classes} classes.")
        except json.JSONDecodeError as e:
            print(f"Warning: Could not decode checking checkpoint file {checking_checkpoint_path}: {e}. Starting checking from scratch for n={n}.")
            last_processed_class_filename = None
            chk_phase_start_time_epoch = time.time() # New start time if corrupted

    # Load generation checkpoint data (for char polys) - ensures char_poly_lookup is populated
    generation_checkpoint_path = os.path.join(n_dir_path, GENERATION_CHECKPOINT_FILE_NAME)
    if os.path.exists(generation_checkpoint_path):
        try:
            with open(generation_checkpoint_path, 'r') as f:
                gen_chkpt_data = json.load(f)
            loaded_class_data = gen_chkpt_data.get("class_data_for_current_n", {})
            char_poly_lookup = {} 
            for h_key, data in loaded_class_data.items():
                split_key = h_key.split('_', 1) 
                current_n_val = int(split_key[0])
                current_h_val = split_key[1]
                if "characteristic_polynomial" in data:
                    char_poly_lookup[(current_n_val, current_h_val)] = data["characteristic_polynomial"]
            print(f"[CHECK RESUME] Loaded characteristic polynomials for n={n} from generation checkpoint.")
        except json.JSONDecodeError as e:
            print(f"Warning: Could not decode generation checkpoint file {generation_checkpoint_path} for checking phase: {e}. Char polys might be re-derived.")


    with overall_results_lock_internal:
        overall_results_summary_internal[n]['status'] = "In Progress (Checking)"
        overall_results_summary_internal[n]['current_progress_checked_classes'] = start_total_checked_classes
        overall_results_summary_internal[n]['yes_classes'] = start_total_yes_classes
        overall_results_summary_internal[n]['no_classes'] = start_total_no_classes
        overall_results_summary_internal[n]['current_status_message'] = f"Starting switching equivalence check for n={n}..."
        overall_results_summary_internal[n]['checking_start_time_epoch'] = chk_phase_start_time_epoch # Store the actual start time


    paths_n = get_computation_paths(n)
    class_files_dir = paths_n["class_dir"]
    
    if not os.path.exists(class_files_dir):
        print(f"Error: Class directory {class_files_dir} not found for n={n}. Skipping checking phase.")
        with overall_results_lock_internal:
            overall_results_summary_internal[n]['status'] = "CHECKING_SKIPPED"
        return

    class_filenames = sorted([f for f in os.listdir(class_files_dir) if f.startswith("class_") and f.endswith(".txt")])

    total_checked_classes = start_total_checked_classes
    total_yes_classes = start_total_yes_classes
    total_no_classes = start_total_no_classes

    # Output file management for the main results (tournaments_n_X.txt or parts)
    current_part_num, latest_file_path_for_n = find_latest_part_file(n) 
    
    output_n_dir = paths_n["n_dir"] 
    if latest_file_path_for_n and os.path.getsize(latest_file_path_for_n) < MAX_FILE_SIZE_BYTES:
        current_output_file = open(latest_file_path_for_n, "a")
    else:
        current_part_num += 1
        new_output_filename = os.path.join(output_n_dir, f"tournaments_n_{n}_part{current_part_num}{FILE_EXTENSION}")
        current_output_file = open(new_output_filename, "w")
    
    # Write header if it's a new file
    if current_part_num == 0 and not latest_file_path_for_n: 
        current_output_file.write(f"\n================= Order n = {n} =================\n\n")
    elif current_part_num > 0: 
        current_output_file.write(f"\n================= Order n = {n} (Part {current_part_num}) =================\n\n")

    # Save checkpoint at the very beginning to establish start_time_epoch
    try:
        with open(checking_checkpoint_path, 'w') as f:
            json.dump({
                "last_processed_class_filename": last_processed_class_filename,
                "total_checked_classes": total_checked_classes,
                "total_yes_classes": total_yes_classes,
                "total_no_classes": total_no_classes,
                "start_time_epoch": chk_phase_start_time_epoch, # Save initial start time
                "last_update_time_epoch": time.time() # And current update time
            }, f)
    except Exception as e:
        print(f"Error saving initial checking checkpoint for n={n}: {e}")

    # Flag to control skipping already processed files
    skip_processing_classes = True if last_processed_class_filename else False
    
    for idx, class_filename in enumerate(class_filenames):
        if skip_processing_classes:
            if class_filename == last_processed_class_filename:
                skip_processing_classes = False # Found the last processed, next iteration will process
                print(f"[CHECK SKIP] Reached last processed class '{class_filename}'. Starting processing from the next class.")
                continue # Skip the last processed one itself to avoid re-writing results for it.
            else:
                if idx % 100 == 0: 
                    print(f"[CHECK SKIP] Skipping class file: {class_filename} for n={n} (already processed)...")
                continue

        # --- Actual processing for new classes ---
        class_filepath = os.path.join(class_files_dir, class_filename)
        h = class_filename[len("class_"): -len(".txt")] 

        adj_matrix_strings_for_class = list(read_adj_matrices_from_file(class_filepath)) 
        
        num_tournaments_in_class = len(adj_matrix_strings_for_class)

        if num_tournaments_in_class == 0:
            print(f"Skipping empty class file: {class_filename}")
            # Still update checkpoint for this class, as it's "processed" (found empty)
            total_checked_classes += 1
            # Save Phase 2 checkpoint after each class is successfully processed
            try:
                with open(checking_checkpoint_path, 'w') as f:
                    json.dump({
                        "last_processed_class_filename": class_filename,
                        "total_checked_classes": total_checked_classes,
                        "total_yes_classes": total_yes_classes,
                        "total_no_classes": total_no_classes,
                        "start_time_epoch": chk_phase_start_time_epoch, # Persist original start time
                        "last_update_time_epoch": time.time() # Update last update time
                    }, f)
            except Exception as e:
                print(f"Error saving checking checkpoint for n={n}: {e}")
            continue 

        char_poly_str = char_poly_lookup.get((n, h))
        if not char_poly_str:
            try:
                first_adj_matrix_str = adj_matrix_strings_for_class[0]
                # Robustly parse the matrix string into a list of lists of integers
                rows_first = [list(map(int, line.split())) for line in first_adj_matrix_str.strip().split('\n')]
                T_first = DiGraph(matrix(ZZ, rows_first))
                seidel_mat_first = seidel_matrix(T_first)
                char_poly_str = str(seidel_mat_first.charpoly())
                print(f"Warning: Characteristic polynomial for (n={n}, h={h}) not found in checkpoint, re-derived.")
            except Exception as e:
                char_poly_str = "Error deriving polynomial"
                print(f"Error deriving characteristic polynomial for (n={n}, h={h}): {e}")
        
        is_switching_equivalent = True
        counterexample_idx1 = -1
        counterexample_idx2 = -1
        
        if num_tournaments_in_class == 1:
            is_switching_equivalent = True 
        else:
            for i in range(num_tournaments_in_class):
                # Robustly parse the matrix string for T1
                matrix_str_T1 = adj_matrix_strings_for_class[i]
                rows_T1 = [list(map(int, line.split())) for line in matrix_str_T1.strip().split('\n')]
                T1 = DiGraph(matrix(ZZ, rows_T1))
                
                for j in range(i + 1, num_tournaments_in_class): 
                    # Robustly parse the matrix string for T2
                    matrix_str_T2 = adj_matrix_strings_for_class[j]
                    rows_T2 = [list(map(int, line.split())) for line in matrix_str_T2.strip().split('\n')]
                    T2 = DiGraph(matrix(ZZ, rows_T2))
                    
                    if not mckay_check(T1, T2): # Now using the corrected mckay_check
                        is_switching_equivalent = False
                        counterexample_idx1 = i
                        counterexample_idx2 = j
                        break 
                
                if not is_switching_equivalent:
                    break 

        current_output_file.write(f"### Charpoly Class {total_checked_classes + 1} ###\n") # Use cumulative count for Class number
        current_output_file.write(f"Characteristic Polynomial: {char_poly_str}\n")
        current_output_file.write(f"Number of tournaments: {num_tournaments_in_class}\n")

        if is_switching_equivalent:
            current_output_file.write("All tournaments in this class are mutually switching equivalent.\n\n\n")
            total_yes_classes += 1
        else:
            current_output_file.write("Not all tournaments in this class are switching equivalent.\n")
            current_output_file.write("Tournaments in this class are NOT all mutually switching equivalent.\n")
            
            # Add counterexample details
            if counterexample_idx1 != -1 and counterexample_idx2 != -1:
                try:
                    # Parse back to DiGraph to get Seidel matrix
                    rows_T1_ce = [list(map(int, line.split())) for line in adj_matrix_strings_for_class[counterexample_idx1].strip().split('\n')]
                    T1_ce = DiGraph(matrix(ZZ, rows_T1_ce))
                    seidel_mat_T1_ce = seidel_matrix(T1_ce)

                    rows_T2_ce = [list(map(int, line.split())) for line in adj_matrix_strings_for_class[counterexample_idx2].strip().split('\n')]
                    T2_ce = DiGraph(matrix(ZZ, rows_T2_ce))
                    seidel_mat_T2_ce = seidel_matrix(T2_ce)

                    current_output_file.write(f"Counterexample: Tournament {counterexample_idx1} (from 0-indexed list) and Tournament {counterexample_idx2} are not switching equivalent.\n")
                    current_output_file.write(f"Tournament {counterexample_idx1} Seidel Matrix:\n")
                    current_output_file.write(str(seidel_mat_T1_ce).replace('\n', '\n  ') + "\n") # Indent matrix
                    current_output_file.write(f"Tournament {counterexample_idx2} Seidel Matrix:\n")
                    current_output_file.write(str(seidel_mat_T2_ce).replace('\n', '\n  ') + "\n\n") # Indent matrix
                except Exception as e:
                    current_output_file.write(f"Error printing counterexample matrices: {e}\n\n")
            else:
                current_output_file.write("No specific counterexample identified or recorded.\n\n")

            total_no_classes += 1
        
        total_checked_classes += 1 # Increment AFTER writing results for the class

        # Check file size and open new part if necessary
        if current_output_file.tell() > MAX_FILE_SIZE_BYTES:
            current_output_file.close()
            current_part_num += 1
            new_output_filename = os.path.join(output_n_dir, f"tournaments_n_{n}_part{current_part_num}{FILE_EXTENSION}")
            current_output_file = open(new_output_filename, "w")
            current_output_file.write(f"\n================= Order n = {n} (Part {current_part_num}) =================\n\n")
        
        # Save Phase 2 checkpoint after each class is successfully processed
        try:
            with open(checking_checkpoint_path, 'w') as f:
                json.dump({
                    "last_processed_class_filename": class_filename,
                    "total_checked_classes": total_checked_classes,
                    "total_yes_classes": total_yes_classes,
                    "total_no_classes": total_no_classes,
                    "start_time_epoch": chk_phase_start_time_epoch, # Persist original start time
                    "last_update_time_epoch": time.time() # Update last update time
                }, f)
            # print(f"[CHECKPOINT] Saved checking checkpoint for n={n} at class: {class_filename}.") # Too verbose
        except Exception as e:
            print(f"Error saving checking checkpoint for n={n}: {e}")

        with overall_results_lock_internal:
            overall_results_summary_internal[n]['current_progress_checked_classes'] = total_checked_classes
            overall_results_summary_internal[n]['yes_classes'] = total_yes_classes
            overall_results_summary_internal[n]['no_classes'] = total_no_classes
            overall_results_summary_internal[n]['status'] = "In Progress (Checking)"
            overall_results_summary_internal[n]['current_status_message'] = f"Checking classes for n={n} ({total_checked_classes}/{len(class_filenames)})."

    current_output_file.close() 
    print(f"[CHECK] Completed checking phase for n={n}. Yes classes: {total_yes_classes}, No classes: {total_no_classes}")

    # Final checkpoint after all classes are processed, marking completion
    try:
        with open(checking_checkpoint_path, 'w') as f:
            json.dump({
                "last_processed_class_filename": "COMPLETE", 
                "total_checked_classes": total_checked_classes,
                "total_yes_classes": total_yes_classes,
                "total_no_classes": total_no_classes,
                "start_time_epoch": chk_phase_start_time_epoch, # Persist original start time
                "last_update_time_epoch": time.time() # Final update time
            }, f)
        print(f"[CHECKPOINT] Saved final checking checkpoint for n={n}, marking as COMPLETE.")
    except Exception as e:
        print(f"Error saving final checking checkpoint for n={n}: {e}")

    with overall_results_lock_internal:
        overall_results_summary_internal[n]['completed'] = True
        overall_results_summary_internal[n]['status'] = "CHECKING_COMPLETE"
        overall_results_summary_internal[n]['current_status_message'] = "" 

# ===================== New Function: Rewriting Results with Counterexamples =====================
def rewrite_results_with_counterexamples(n: int):
    """
    Rewrites the output .txt files for a given order 'n' to include
    Seidel matrices for counterexamples in "NOT switching equivalent" classes.
    This function assumes the 'classes' subdirectory with raw tournament data exists.
    """
    print(f"[REWRITE] Starting rewrite for n={n} to add counterexamples...")
    
    n_dir_path = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n}")
    class_files_dir = os.path.join(n_dir_path, "classes")

    if not os.path.exists(class_files_dir):
        print(f"[REWRITE ERROR] Class directory {class_files_dir} not found for n={n}. Cannot rewrite without raw data.")
        return

    class_filenames = sorted([f for f in os.listdir(class_files_dir) if f.startswith("class_") and f.endswith(".txt")])
    
    all_class_output_strings = []
    total_no_classes_rewritten = 0 # Track for logging

    # Load generation checkpoint data for char polys
    # This is crucial for getting the characteristic polynomials without re-deriving them
    generation_checkpoint_path = os.path.join(n_dir_path, GENERATION_CHECKPOINT_FILE_NAME)
    current_char_poly_lookup = {}
    if os.path.exists(generation_checkpoint_path):
        try:
            with open(generation_checkpoint_path, 'r') as f:
                gen_chkpt_data = json.load(f)
            loaded_class_data = gen_chkpt_data.get("class_data_for_current_n", {})
            for h_key, data in loaded_class_data.items():
                split_key = h_key.split('_', 1)
                current_n_val = int(split_key[0])
                current_h_val = split_key[1]
                if current_n_val == n and "characteristic_polynomial" in data:
                    current_char_poly_lookup[current_h_val] = data["characteristic_polynomial"]
        except json.JSONDecodeError as e:
            print(f"[REWRITE WARNING] Could not load generation checkpoint for n={n}: {e}. Char polys might be re-derived.")

    for idx, class_filename in enumerate(class_filenames):
        class_filepath = os.path.join(class_files_dir, class_filename)
        h = class_filename[len("class_"): -len(".txt")] 

        adj_matrix_strings_for_class = list(read_adj_matrices_from_file(class_filepath)) 
        num_tournaments_in_class = len(adj_matrix_strings_for_class)

        char_poly_str = current_char_poly_lookup.get(h)
        if not char_poly_str:
            # Fallback if char poly not in checkpoint (shouldn't happen if generation completed)
            if adj_matrix_strings_for_class:
                try:
                    rows_first = [list(map(int, line.split())) for line in adj_matrix_strings_for_class[0].strip().split('\n')]
                    T_first = DiGraph(matrix(ZZ, rows_first))
                    char_poly_str = str(seidel_matrix(T_first).charpoly())
                except Exception as e:
                    char_poly_str = "Error deriving polynomial"
                    print(f"[REWRITE WARNING] Error re-deriving char poly for n={n}, class {h}: {e}")
            else:
                char_poly_str = "N/A (empty class)"

        is_switching_equivalent = True
        counterexample_idx1 = -1
        counterexample_idx2 = -1
        
        if num_tournaments_in_class == 1:
            is_switching_equivalent = True 
        else:
            for i in range(num_tournaments_in_class):
                matrix_str_T1 = adj_matrix_strings_for_class[i]
                rows_T1 = [list(map(int, line.split())) for line in matrix_str_T1.strip().split('\n')]
                T1 = DiGraph(matrix(ZZ, rows_T1))
                
                for j in range(i + 1, num_tournaments_in_class): 
                    matrix_str_T2 = adj_matrix_strings_for_class[j]
                    rows_T2 = [list(map(int, line.split())) for line in matrix_str_T2.strip().split('\n')]
                    T2 = DiGraph(matrix(ZZ, rows_T2))
                    
                    if not mckay_check(T1, T2):
                        is_switching_equivalent = False
                        counterexample_idx1 = i
                        counterexample_idx2 = j
                        break 
                
                if not is_switching_equivalent:
                    break 

        # Build the string for this class
        class_output_lines = []
        class_output_lines.append(f"### Charpoly Class {idx + 1} ###") # Use 1-indexed for output
        class_output_lines.append(f"Characteristic Polynomial: {char_poly_str}")
        class_output_lines.append(f"Number of tournaments: {num_tournaments_in_class}")

        if is_switching_equivalent:
            class_output_lines.append("All tournaments in this class are mutually switching equivalent.")
        else:
            class_output_lines.append("Not all tournaments in this class are switching equivalent.")
            class_output_lines.append("Tournaments in this class are NOT all mutually switching equivalent.")
            total_no_classes_rewritten += 1 # Increment counter for logging

            if counterexample_idx1 != -1 and counterexample_idx2 != -1:
                try:
                    rows_T1_ce = [list(map(int, line.split())) for line in adj_matrix_strings_for_class[counterexample_idx1].strip().split('\n')]
                    T1_ce = DiGraph(matrix(ZZ, rows_T1_ce))
                    seidel_mat_T1_ce = seidel_matrix(T1_ce)

                    rows_T2_ce = [list(map(int, line.split())) for line in adj_matrix_strings_for_class[counterexample_idx2].strip().split('\n')]
                    T2_ce = DiGraph(matrix(ZZ, rows_T2_ce))
                    seidel_mat_T2_ce = seidel_matrix(T2_ce)

                    class_output_lines.append(f"Counterexample: Tournament {counterexample_idx1} (from 0-indexed list) and Tournament {counterexample_idx2} are not switching equivalent.")
                    class_output_lines.append(f"Tournament {counterexample_idx1} Seidel Matrix:\n")
                    class_output_lines.append(str(seidel_mat_T1_ce).replace('\n', '\n  ')) # Indent matrix
                    class_output_lines.append(f"Tournament {counterexample_idx2} Seidel Matrix:\n")
                    class_output_lines.append(str(seidel_mat_T2_ce).replace('\n', '\n  ')) # Indent matrix
                except Exception as e:
                    class_output_lines.append(f"Error printing counterexample matrices: {e}")
            else:
                class_output_lines.append("No specific counterexample identified or recorded.")
        
        all_class_output_strings.append("\n".join(class_output_lines) + "\n\n")

    # Now, write all collected class strings to the output file(s) for this 'n'
    # This overwrites the existing file(s)
    output_filename_base = os.path.join(n_dir_path, f"tournaments_n_{n}{FILE_EXTENSION}")
    
    current_output_file_path = output_filename_base
    current_part_num = 0
    file_idx = 0

    # Open the first file in write mode, which truncates it
    current_output_file = open(current_output_file_path, "w")
    current_output_file.write(f"\n================= Order n = {n} =================\n\n")

    for class_str in all_class_output_strings:
        # Check if adding this class string would exceed the file size limit
        # This is a simplified check; for precise splitting, you'd need to consider
        # the exact size of the header and previous content.
        if current_output_file.tell() + len(class_str.encode('utf-8')) > MAX_FILE_SIZE_BYTES and current_part_num == 0:
            current_output_file.close()
            current_part_num += 1
            current_output_file_path = os.path.join(n_dir_path, f"tournaments_n_{n}_part{current_part_num}{FILE_EXTENSION}")
            current_output_file = open(current_output_file_path, "w")
            current_output_file.write(f"\n================= Order n = {n} (Part {current_part_num}) =================\n\n")
        elif current_output_file.tell() + len(class_str.encode('utf-8')) > MAX_FILE_SIZE_BYTES and current_part_num > 0:
             current_output_file.close()
             current_part_num += 1
             current_output_file_path = os.path.join(n_dir_path, f"tournaments_n_{n}_part{current_part_num}{FILE_EXTENSION}")
             current_output_file = open(current_output_file_path, "w")
             current_output_file.write(f"\n================= Order n = {n} (Part {current_part_num}) =================\n\n")

        current_output_file.write(class_str)

    current_output_file.close()
    print(f"[REWRITE] Finished rewriting results for n={n}. Found {total_no_classes_rewritten} 'NOT switching equivalent' classes.")

# ===================== Helper to select next N to process =====================
def select_next_n_to_process(current_progress_info):
    """
    Selects the next 'n' value to process based on its completion status.
    Prioritizes incomplete generation, then incomplete checking.
    """
    # Orders to iterate through (e.g., from N_MIN to N_MAX)
    # Assuming N_MAX is defined in main.
    N_MIN_GLOBAL = 1 # Define a global or pass N_MIN, N_MAX as arguments
    N_MAX_GLOBAL = 18 # Define a global or pass N_MIN, N_MAX as arguments

    # Check for orders that are PENDING or In Progress (Generation)
    for n_val in range(N_MIN_GLOBAL, N_MAX_GLOBAL + 1):
        status = current_progress_info.get(n_val, {}).get('status', 'PENDING')
        if status in ["PENDING", "In Progress (Generation)"]:
            return n_val

    # If all generations are complete, check for orders that are GENERATION_ONLY or In Progress (Checking)
    for n_val in range(N_MIN_GLOBAL, N_MAX_GLOBAL + 1):
        status = current_progress_info.get(n_val, {}).get('status', 'PENDING')
        if status in ["GENERATION_ONLY", "In Progress (Checking)"]:
            return n_val
            
    return None # All orders are CHECKING_COMPLETE

# ===================== Main Execution Loop =====================
def main():
    N_MIN = 1
    N_MAX = 18 # Example, set your desired max 'n'
    
    # Ensure the root output directory exists
    os.makedirs(ROOT_OUTPUT_BASE_DIR, exist_ok=True)

    # --- Special Mode: Rewrite completed orders with counterexamples ---
    if REWRITE_COMPLETED_ORDERS:
        print("\n--- Activating REWRITE_COMPLETED_ORDERS mode ---")
        for n_val in REWRITE_TARGET_ORDERS:
            # Check if the n-directory exists and has class files
            n_dir_path = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n_val}")
            class_files_dir = os.path.join(n_dir_path, "classes")
            
            if os.path.exists(class_files_dir) and os.listdir(class_files_dir):
                rewrite_results_with_counterexamples(n_val)
            else:
                print(f"[REWRITE SKIP] Skipping rewrite for n={n_val}: no class files found in {class_files_dir}.")
            update_readme_main() # Update README after each rewrite
        print("\n--- Finished REWRITE_COMPLETED_ORDERS mode. Proceeding to normal operation ---")
        # You might want to set REWRITE_COMPLETED_ORDERS = False here
        # or exit the script if this was a one-off task.
        # For now, it will continue to the normal loop.

    # --- Normal Processing Loop ---
    
    # Determine max_n_from_primary_output_dir by checking existing checkpoints
    # This is done AFTER any potential rewriting, so it picks up the latest state.
    max_n_from_primary_output_dir = 0
    for n_dir_name in os.listdir(ROOT_OUTPUT_BASE_DIR):
        if not n_dir_name.startswith('n') or not n_dir_name[1:].isdigit():
            continue
        current_order = int(n_dir_name[1:])
        n_dir_path = os.path.join(ROOT_OUTPUT_BASE_DIR, n_dir_name)
        checking_checkpoint_path = os.path.join(n_dir_path, CHECKING_CHECKPOINT_FILE_NAME)
        
        if os.path.exists(checking_checkpoint_path):
            try:
                with open(checking_checkpoint_path, 'r') as f:
                    chkpt_data = json.load(f)
                if chkpt_data.get("last_processed_class_filename") == "COMPLETE":
                    max_n_from_primary_output_dir = max(max_n_from_primary_output_dir, current_order)
            except json.JSONDecodeError:
                pass # Corrupt checkpoint, ignore

    print(f"Starting normal computation from order n = {N_MIN} (max completed n in primary output: {max_n_from_primary_output_dir})")

    start_n_computation = max(N_MIN, max_n_from_primary_output_dir + 1)
    if N_MIN > max_n_from_primary_output_dir + 1:
         print(f"Warning: N_MIN ({N_MIN}) is greater than max_n_from_primary_output_dir + 1 ({max_n_from_primary_output_dir + 1}). Computation will start from N_MIN.")

    # Main runner loop interval
    MAIN_RUNNER_LOOP_INTERVAL_SECONDS = 1 

    while True:
        try:
            # 1. Collect all current data and progress
            # This call fetches data from ROOT_OUTPUT_BASE_DIR AND PRE_EXISTING_RESULTS_DIR
            results, found_orders, current_progress_info, completed_ns_in_primary_output = collect_all_data_for_readme()
            
            # 2. Select the next 'n' to work on
            next_n_to_process = select_next_n_to_process(current_progress_info)

            if next_n_to_process is None or next_n_to_process > N_MAX:
                print("\n[RUNNER] All specified tournament orders appear to be complete or N_MAX reached. Idling...")
                # Still update README one last time on idle to reflect final state
                update_readme_main() 
                time.sleep(MAIN_RUNNER_LOOP_INTERVAL_SECONDS) # Sleep when idle
                continue # Re-check status periodically even when idle

            print(f"\n[RUNNER] Processing order n = {next_n_to_process}...")

            n_info = current_progress_info.get(next_n_to_process, {})
            current_status = n_info.get('status', 'PENDING')

            # 3. Determine action based on current status
            if current_status == "PENDING" or current_status == "In Progress (Generation)":
                run_sequential(next_n_to_process) # Use run_sequential for actual generation
            elif current_status == "GENERATION_ONLY" or current_status == "In Progress (Checking)":
                run_checking_phase(next_n_to_process) # Use run_checking_phase for actual checking
            elif current_status == "CHECKING_COMPLETE":
                print(f"[RUNNER] Order n = {next_n_to_process} is already complete. Skipping.")
                # This should ideally not be reached if select_next_n_to_process works correctly,
                # but good for a sanity check.

            # 4. Update README after each significant step
            update_readme_main()

            print(f"[RUNNER] Loop complete for n={next_n_to_process}. Waiting {MAIN_RUNNER_LOOP_INTERVAL_SECONDS} seconds for next check.")
            time.sleep(MAIN_RUNNER_LOOP_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("\n[RUNNER] KeyboardInterrupt detected. Shutting down gracefully...")
            update_readme_main() # Final README update on exit
            sys.exit(0) # Exit the script
        except Exception as e:
            print(f"[RUNNER ERROR] An error occurred in the main runner loop: {e}")
            print("[RUNNER] Retrying after 60 seconds...")
            time.sleep(60) # Longer sleep on error


if __name__ == "__main__":
    main()