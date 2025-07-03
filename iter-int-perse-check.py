import os
import re
import time
import hashlib
import threading
from collections import defaultdict
from datetime import datetime
import json
from sage.all import *

# ===================== Environment Variables =====================
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

# ===================== Global Constants and Paths =====================
ROOT_OUTPUT_BASE_DIR = "tournament_outputs_by_class"
FILE_EXTENSION = ".txt"

# This script does not use PRE_EXISTING_RESULTS_DIR directly for computation flow.
# It's kept here for consistency if you decide to re-integrate it later,
# but it's primarily used by readme_generator.py now.
PRE_EXISTING_RESULTS_DIR = "/Users/lyuk/Downloads/cospectral-tournaments/tournament_outputs_1-10" 

os.makedirs(ROOT_OUTPUT_BASE_DIR, exist_ok=True)

# Data for number of non-isomorphic tournaments on n nodes (for progress calculation)
NON_ISO_TOURNAMENTS = {
    1: 1, 2: 1, 3: 2, 4: 4, 5: 12, 6: 56, 7: 456, 8: 6880,
    9: 191536, 10: 9733056, 11: 903753248, 12: 154108311168,
    13: 48542114686912, 14: 28401423719122304, 15: 31021002160355166848,
    16: 63530415842308265100288, 17: 244912778438520759443245823,
    18: 1783398846284777975419600287232
}

# File Size Limit for output .txt files (10 MB)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024 

# Checkpointing constants
CHECKPOINT_INTERVAL = 100000 # Save checkpoint every 100,000 tournaments generated (Phase 1)
GENERATION_CHECKPOINT_FILE_NAME = "generation_checkpoint.json" # Checkpoint for Phase 1
CHECKING_CHECKPOINT_FILE_NAME = "checking_checkpoint.json" # Checkpoint for Phase 2

# ===================== Data Storage for Real-time Progress (Internal to this script) =====================
# These are now only for internal logging/display within this runner script, not for README generation.
overall_results_summary_internal = {} 
overall_results_lock_internal = threading.Lock() 
currently_processing_n_internal = None

# Global variable to store char poly from Phase 1 checkpoint for easy access in Phase 2
char_poly_lookup = {} 

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

def find_latest_part_file(n):
    """
    Finds the latest part file for the main output (tournaments_n_X_partY.txt)
    for a given n.
    """
    output_dir = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n}")
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
            
            print(f"[RESUME] Resuming n={n} from checkpoint. Already processed {start_total_tournaments_generated} tournaments.")
        except json.JSONDecodeError as e:
            print(f"Warning: Could not decode checkpoint file {checkpoint_path}: {e}. Starting from scratch for n={n}.")
            start_total_tournaments_generated = 0
            initial_class_data_for_current_n = defaultdict(lambda: {"count": 0, "characteristic_polynomial": ""})
            initial_generated_charpoly_hashes = set()
            
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
            'current_status_message': f"Generating tournaments for n={n}..."
        }

    print(f"[GEN/CHK] Starting processing for n={n}")
    
    paths_n = get_computation_paths(n)
    os.makedirs(paths_n["class_dir"], exist_ok=True)

    tournaments_gen = digraphs.tournaments_nauty(n)
    
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
                        "class_data_for_current_n": serializable_class_data
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
    
    try:
        with open(checkpoint_path, 'w') as f:
            serializable_class_data = {f"{k[0]}_{k[1]}": v for k, v in class_data_for_current_n.items()}
            json.dump({
                "total_tournaments_generated": total_tournaments_generated,
                "class_data_for_current_n": serializable_class_data
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
    
    # Attempt to load checking checkpoint
    if os.path.exists(checking_checkpoint_path):
        try:
            with open(checking_checkpoint_path, 'r') as f:
                chkpt_data = json.load(f)
            last_processed_class_filename = chkpt_data.get("last_processed_class_filename")
            start_total_checked_classes = chkpt_data.get("total_checked_classes", 0)
            start_total_yes_classes = chkpt_data.get("total_yes_classes", 0)
            start_total_no_classes = chkpt_data.get("total_no_classes", 0)
            
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
            current_output_file.write("Tournaments in this class are NOT all mutually switching equivalent.\n\n\n")
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
                    "total_no_classes": total_no_classes
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
                "total_no_classes": total_no_classes
            }, f)
        print(f"[CHECKPOINT] Saved final checking checkpoint for n={n}, marking as COMPLETE.")
    except Exception as e:
        print(f"Error saving final checking checkpoint for n={n}: {e}")

    with overall_results_lock_internal:
        overall_results_summary_internal[n]['completed'] = True
        overall_results_summary_internal[n]['status'] = "CHECKING_COMPLETE"
        overall_results_summary_internal[n]['current_status_message'] = "" 


# ===================== Main Execution Loop =====================
def main():
    N_MIN = 1
    N_MAX = 10 # Example, set your desired max 'n'
    
    # Determine max_n_from_primary_output_dir by checking existing checkpoints
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

    print(f"Starting computation from order n = {N_MIN} (max pre-existing n: {max_n_from_primary_output_dir})")

    start_n_computation = max(N_MIN, max_n_from_primary_output_dir + 1)
    if N_MIN > max_n_from_primary_output_dir + 1:
         print(f"Warning: N_MIN ({N_MIN}) is greater than max_n_from_primary_output_dir + 1 ({max_n_from_primary_output_dir + 1}). Computation will start from N_MIN.")

    for n_val in range(start_n_computation, N_MAX + 1):
        # Determine status from checkpoints for skipping phases
        current_n_status_for_loop_check = 'PENDING'
        n_dir_path = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n_val}")
        checking_checkpoint_path = os.path.join(n_dir_path, CHECKING_CHECKPOINT_FILE_NAME)
        generation_checkpoint_path = os.path.join(n_dir_path, GENERATION_CHECKPOINT_FILE_NAME)

        if os.path.exists(checking_checkpoint_path):
            try:
                with open(checking_checkpoint_path, 'r') as f:
                    chkpt_data = json.load(f)
                if chkpt_data.get("last_processed_class_filename") == "COMPLETE":
                    current_n_status_for_loop_check = "CHECKING_COMPLETE"
            except json.JSONDecodeError:
                pass
        
        if current_n_status_for_loop_check != "CHECKING_COMPLETE" and os.path.exists(generation_checkpoint_path):
            try:
                with open(generation_checkpoint_path, 'r') as f:
                    gen_chkpt_data = json.load(f)
                total_gen = gen_chkpt_data.get("total_tournaments_generated", 0)
                expected_total_tournaments = NON_ISO_TOURNAMENTS.get(n_val, 0)
                if expected_total_tournaments > 0 and total_gen >= expected_total_tournaments:
                    current_n_status_for_loop_check = "GENERATION_ONLY"
            except json.JSONDecodeError:
                pass


        # Phase 1: Generation
        print(f"\n--- Starting Generation Phase for n={n_val} ---")
        if current_n_status_for_loop_check in ["GENERATION_ONLY", "CHECKING_COMPLETE"]:
            print(f"Skipping Generation Phase for n={n_val} as it was already marked as complete or generation-only complete.")
        else:
            run_sequential(n_val)

        # Re-determine status after generation phase in case it just completed
        current_n_status_for_loop_check = 'PENDING'
        if os.path.exists(checking_checkpoint_path):
            try:
                with open(checking_checkpoint_path, 'r') as f:
                    chkpt_data = json.load(f)
                if chkpt_data.get("last_processed_class_filename") == "COMPLETE":
                    current_n_status_for_loop_check = "CHECKING_COMPLETE"
            except json.JSONDecodeError:
                pass
        
        if current_n_status_for_loop_check != "CHECKING_COMPLETE" and os.path.exists(generation_checkpoint_path):
            try:
                with open(generation_checkpoint_path, 'r') as f:
                    gen_chkpt_data = json.load(f)
                total_gen = gen_chkpt_data.get("total_tournaments_generated", 0)
                expected_total_tournaments = NON_ISO_TOURNAMENTS.get(n_val, 0)
                if expected_total_tournaments > 0 and total_gen >= expected_total_tournaments:
                    current_n_status_for_loop_check = "GENERATION_ONLY"
            except json.JSONDecodeError:
                pass


        # Phase 2: Checking for Switching Equivalence
        print(f"\n--- Starting Checking Phase for n={n_val} ---")
        if current_n_status_for_loop_check == "GENERATION_ONLY":
            run_checking_phase(n_val)
        elif current_n_status_for_loop_check == "CHECKING_COMPLETE":
            print(f"Skipping Checking Phase for n={n_val} as it was already marked as fully complete.")
        else:
            print(f"Skipping Checking Phase for n={n_val} as Generation was not marked as fully complete or status is not appropriate.")


    print("\nAll requested computations completed.")

if __name__ == "__main__":
    main()