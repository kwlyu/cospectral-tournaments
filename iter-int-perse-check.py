import os
import re
import time
import hashlib
import threading
from collections import defaultdict
from datetime import datetime
from sage.all import *

# ===================== Environment Variables =====================
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

# ===================== Global Constants and Paths =====================
# This is the base directory where 'n' specific folders (n3, n4, etc.) will be created.
# The README.md will also live directly within this directory.
ROOT_OUTPUT_BASE_DIR = "tournament_outputs_by_class"
README_PATH = os.path.join(ROOT_OUTPUT_BASE_DIR, "README.md")
FILE_EXTENSION = ".txt"

# !!! IMPORTANT: SET THIS TO THE PATH OF YOUR FOLDER WITH EXISTING RESULTS !!!
# Example: PRE_EXISTING_RESULTS_DIR = "/Users/YourUser/Documents/OldTournamentResults"
PRE_EXISTING_RESULTS_DIR = "/Users/lyuk/Downloads/cospectral-tournaments/tournament_outputs_new" # Placeholder, update this path

os.makedirs(ROOT_OUTPUT_BASE_DIR, exist_ok=True)
if PRE_EXISTING_RESULTS_DIR and not os.path.exists(PRE_EXISTING_RESULTS_DIR):
    print(f"Warning: PRE_EXISTING_RESULTS_DIR '{PRE_EXISTING_RESULTS_DIR}' does not exist. No pre-existing results will be loaded.")

# Data for number of non-isomorphic tournaments on n nodes (for progress calculation)
NON_ISO_TOURNAMENTS = {
    1: 1, 2: 1, 3: 2, 4: 4, 5: 12, 6: 56, 7: 456, 8: 6880,
    9: 191536, 10: 9733056, 11: 903753248, 12: 154108311168,
    13: 48542114686912, 14: 28401423719122304, 15: 31021002160355166848,
    16: 63530415842308265100288, 17: 244912778438520759443245823,
    18: 1783398846284777975419600287232
}

# README Formatting Constants
MAX_POLY_ROWS_PER_ALIGN_ENV = 10  # Max polynomials in one $$aligned$$ block
MAX_ALIGN_ENVS_PER_SUBSECTION = 5 # Max aligned blocks before starting new collapsible section

# File Size Limit for output .txt files (10 MB)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024 

# ===================== Regex Patterns for README Parsing =====================
charpoly_pattern = re.compile(r'Characteristic Polynomial:\s*(.+)')
switching_yes_pattern = re.compile(r'All tournaments in this class are mutually switching equivalent')
trivial_switching_pattern = re.compile(r'Only one tournament - trivially switching equivalent')
switching_no_pattern = re.compile(r'Not all tournaments in this class are switching equivalent')
not_equiv_pattern = re.compile(r'Tournaments \d+ and \d+ are NOT switching equivalent')

# ===================== Data Storage for Real-time Progress =====================
overall_results_summary = {}
overall_results_lock = threading.Lock()
currently_processing_n = None

# ===================== Core Computation Utilities =====================
def seidel_matrix(T):
    A = T.adjacency_matrix()
    return A - A.transpose()

def mckay_matrix(S):
    S = seidel_matrix(S)
    n = S.nrows()
    D = zero_matrix(2 * n)
    for i in range(n):
        for j in range(n):
            if S[i, j] == 1:
                D[2*i, 2*j] = 1
                D[2*i+1, 2*j+1] = 1
            elif S[i, j] == -1:
                D[2*i, 2*j+1] = 1
                D[2*i+1, 2*j] = 1
    return D

def mckay_check(A, B):
    return DiGraph(A).is_isomorphic(DiGraph(B))

def hash_charpoly(poly):
    return hashlib.md5(str(poly).encode()).hexdigest()[:8]

# ===================== File Path Helpers for Main Computation =====================
def get_computation_paths(n, h=None):
    base = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n}")
    return {
        "class_dir": os.path.join(base, "classes"),
        "class_file": os.path.join(base, "classes", f"class_{h}.txt") if h else None,
        # The root_output_file path is now dynamically handled by _get_or_create_file_handle
        # No longer a single static path for all output of n.
    }

# Helper to find the latest part file and its number for resuming
def find_latest_part_file(n):
    output_dir = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n}")
    if not os.path.exists(output_dir):
        return 0, None # No parts exist, start with 'base' file (part 0 convention)

    max_part = 0 # 0 means the base file (e.g., tournaments_n_X.txt)
    latest_filepath = None
    
    # Check for base file first
    base_file = os.path.join(output_dir, f"tournaments_n_{n}{FILE_EXTENSION}")
    if os.path.exists(base_file):
        max_part = 0
        latest_filepath = base_file

    # Check for part files
    for filename in os.listdir(output_dir):
        match = re.match(rf'tournaments_n_{n}_part(\d+){re.escape(FILE_EXTENSION)}$', filename)
        if match:
            part_num = int(match.group(1))
            if part_num > max_part: # If a part file has a higher number, it's the latest
                max_part = part_num
                latest_filepath = os.path.join(output_dir, filename)
    
    return max_part, latest_filepath

# ===================== Main Logic (sequential execution for each n) =====================
def run_sequential(n):
    global currently_processing_n
    
    with overall_results_lock:
        currently_processing_n = n
        overall_results_summary[n] = {
            'completed': False,
            'status': 'In Progress',
            'yes_classes': 0, 'no_classes': 0, 'total_classes': 0,
            'current_progress_generated_tournaments': 0,
            'current_progress_checked_classes': 0,
            'current_status_message': f"Generating tournaments for n={n}..."
        }

    print(f"[GEN/CHK] Starting processing for n={n}")
    
    paths_n = get_computation_paths(n)
    os.makedirs(paths_n["class_dir"], exist_ok=True)
    
    class_data_for_current_n = defaultdict(lambda: {"tournaments": [], "checked_pairs": set(), "result_string": ""})

    # --- Generation Phase ---
    generated_charpoly_hashes = set()
    total_tournaments_generated = 0
    
    tournaments_gen = digraphs.tournaments_nauty(n)
    # REMOVED: all_tournaments = list(tournaments_gen) # This line was causing memory issues
    
    # Iterate directly over the generator to avoid memory exhaustion
    for i, T in enumerate(tournaments_gen): 
        seidel_mat = seidel_matrix(T)
        poly = seidel_mat.charpoly()
        h = hash_charpoly(poly)
        
        class_info = class_data_for_current_n[(n, h)]
        class_info["tournaments"].append(T) # Still collecting tournaments for THIS class, not all
        class_info["characteristic_polynomial"] = str(poly)
        generated_charpoly_hashes.add((n, h))
        total_tournaments_generated += 1

        with open(get_computation_paths(n, h=h)["class_file"], "a") as f:
            f.write(str(T.adjacency_matrix()) + "\n\n")
        
        with overall_results_lock:
            overall_results_summary[n]['current_progress_generated_tournaments'] = total_tournaments_generated
            overall_results_summary[n]['total_classes'] = len(generated_charpoly_hashes) 
            expected_total_tournaments = NON_ISO_TOURNAMENTS.get(n, 0)
            if expected_total_tournaments == 0:
                overall_results_summary[n]['current_status_message'] = f"Generated {total_tournaments_generated} tournaments for n={n} (total known: N/A)."
            else:
                gen_percent = (total_tournaments_generated / expected_total_tournaments) * 100
                overall_results_summary[n]['current_status_message'] = f"Generated {total_tournaments_generated}/{expected_total_tournaments} tournaments for n={n} ({gen_percent:.2f}%)."
            
    print(f"[GEN/CHK] Done generating for n={n}, starting full check.")
    
    with overall_results_lock:
        overall_results_summary[n]['current_status_message'] = f"Checking classes for n={n}..."

    # --- File Management for Output (New logic for splitting) ---
    current_part_num, latest_file_path_for_n = find_latest_part_file(n)
    current_output_file_handle = None
    current_output_filepath = latest_file_path_for_n if latest_file_path_for_n else ""

    def _get_or_create_file_handle_for_n():
        nonlocal current_output_file_handle, current_output_filepath, current_part_num

        # If a file is already open and not yet full, return it
        if current_output_file_handle and os.path.getsize(current_output_filepath) < MAX_FILE_SIZE_BYTES:
            return current_output_file_handle

        # If an old file was open and is now full, close it
        if current_output_file_handle:
            current_output_file_handle.close()
            print(f"[FILE] Closed {current_output_filepath} (exceeded size limit).")
            # If the last file was the base file (part 0), the next is part 1.
            # Otherwise, increment the part number.
            current_part_num = 1 if current_part_num == 0 else current_part_num + 1 

        # Determine the filename for the new/current part
        if current_part_num == 0: # Convention: 0 means the base file (no _partX suffix)
            file_name = f"tournaments_n_{n}{FILE_EXTENSION}"
        else: # For subsequent parts
            file_name = f"tournaments_n_{n}_part{current_part_num}{FILE_EXTENSION}"

        output_dir = os.path.join(ROOT_OUTPUT_BASE_DIR, f"n{n}")
        os.makedirs(output_dir, exist_ok=True)
        current_output_filepath = os.path.join(output_dir, file_name)

        mode = "a" # Always append to an existing file or create a new one
        try:
            current_output_file_handle = open(current_output_filepath, mode)
            # Write header ONLY if the file was just created or is empty
            if os.path.getsize(current_output_filepath) == 0:
                current_output_file_handle.write(f"================= Order n = {n} ================\n\n")
            print(f"[FILE] Opened/re-opened {current_output_filepath} for writing (part {current_part_num if current_part_num != 0 else 'base'}).")
            return current_output_file_handle
        except Exception as e:
            print(f"Error opening file {current_output_filepath}: {e}")
            raise # Re-raise to stop computation if file can't be opened

    # --- Checking Phase ---
    # aggregated_output_parts is no longer needed since we write incrementally
    
    total_yes_classes = 0
    total_no_classes = 0
    all_classes_are_switching_equivalent_for_n = True

    sorted_hashes = sorted(list(generated_charpoly_hashes))

    for idx, (n_key, h) in enumerate(sorted_hashes):
        class_info = class_data_for_current_n[(n_key, h)]
        tourn_list = class_info["tournaments"]
        checked_pairs = class_info["checked_pairs"]
        char_poly_str = class_info["characteristic_polynomial"]

        class_header = f"### Charpoly Class {idx + 1} ###\n"
        class_header += f"Characteristic Polynomial: {char_poly_str}\n"
        class_header += f"Number of tournaments: {len(tourn_list)}\n"
        
        result_message = ""
        is_current_class_all_equivalent = True

        if len(tourn_list) < 2:
            result_message = "All tournaments in this class are mutually switching equivalent.\n"
        else:
            for i in range(len(tourn_list)):
                for j in range(i + 1, len(tourn_list)):
                    if (i, j) not in checked_pairs:
                        A = mckay_matrix(tourn_list[i])
                        B = mckay_matrix(tourn_list[j])
                        if not mckay_check(A, B):
                            result_message = f"Tournaments {i} and {j} are NOT switching equivalent (by McKay matrix isomorphism).\n"
                            # We omit printing large matrices to the main output file to save space
                            # result_message += f"Tournament {i}:\n{tourn_list[i].adjacency_matrix()}\n"
                            # result_message += f"Tournament {j}:\n{tourn_list[j].adjacency_matrix()}\n"
                            
                            result_message += "Not all tournaments in this class are switching equivalent.\n"
                            result_message += "Found a pair that is NOT switching equivalent. Skipping further checks in this class.\n"
                            is_current_class_all_equivalent = False
                            all_classes_are_switching_equivalent_for_n = False
                            break
                        checked_pairs.add((i, j))
                if not is_current_class_all_equivalent:
                    break
            
            if is_current_class_all_equivalent:
                result_message = "All tournaments in this class are mutually switching equivalent.\n"
        
        # Write directly to the current output file part
        output_file_handle = _get_or_create_file_handle_for_n()
        output_file_handle.write(class_header + result_message + "\n")

        if is_current_class_all_equivalent:
            total_yes_classes += 1
        else:
            total_no_classes += 1

        with overall_results_lock:
            overall_results_summary[n]['current_progress_checked_classes'] = idx + 1
            overall_results_summary[n]['yes_classes'] = total_yes_classes
            overall_results_summary[n]['no_classes'] = total_no_classes
            
            checked_percent = ((idx + 1) / len(sorted_hashes)) * 100
            overall_results_summary[n]['current_status_message'] = f"Checked {idx + 1}/{len(sorted_hashes)} charpoly classes for n={n} ({checked_percent:.2f}%)."


    # --- Final cleanup for this n's files ---
    if current_output_file_handle:
        current_output_file_handle.close()
        print(f"[FILE] Closed final part file {current_output_filepath} for n={n}.")

    print(f"[GEN/CHK] Done processing for n={n}. Results written to {os.path.join(ROOT_OUTPUT_BASE_DIR, f'n{n}')} files.")

    with overall_results_lock:
        overall_results_summary[n]['completed'] = True
        overall_results_summary[n]['status'] = "✅ YES" if all_classes_are_switching_equivalent_for_n else "❌ NO"
        overall_results_summary[n]['current_status_message'] = "" # Clear temporary message
        overall_results_summary[n]['total_classes'] = len(generated_charpoly_hashes)

    with overall_results_lock:
        if currently_processing_n == n:
            currently_processing_n = None

# ===================== README Parsing Helper =====================
def _parse_lines_into_results(lines, order, results_dict):
    """
    Parses lines from a single results file (or combined parts) and updates the results_dict.
    It appends new, unique polynomials to the lists.
    """
    current_poly = None
    for line in lines:
        poly_match = charpoly_pattern.search(line)
        if poly_match:
            current_poly = poly_match.group(1).strip()
            continue

        if switching_yes_pattern.search(line) or trivial_switching_pattern.search(line):
            if current_poly:
                if current_poly not in results_dict[order]['yes'] and current_poly not in results_dict[order]['no']:
                    results_dict[order]['yes'].append(current_poly)
                current_poly = None
            continue
        
        if switching_no_pattern.search(line) or not_equiv_pattern.search(line):
            if current_poly:
                if current_poly not in results_dict[order]['yes'] and current_poly not in results_dict[order]['no']:
                    results_dict[order]['no'].append(current_poly)
                current_poly = None
            continue

# ===================== README Generation Logic =====================
def generate_progress_bar(percent: float, width: int = 10, style='blocks'):
    filled = int(percent * width)
    empty = width - filled
    if style == 'emoji':
        return '🟩' * filled + '⬜' * empty
    elif style == 'blocks':
        return '█' * filled + '░' * empty
    else:
        return '⬛' * filled + '⬜' * empty

def collect_results_for_readme():
    """
    Collects results from ROOT_OUTPUT_BASE_DIR and PRE_EXISTING_RESULTS_DIR.
    Handles multipart files for both sources.
    Returns a dictionary of results grouped by order, a set of found orders,
    the maximum 'n' found in the pre-existing results directory,
    and a set of 'n' values that were confirmed as completed by pre-existing files.
    """
    found_orders = set()
    results = defaultdict(lambda: {'yes': [], 'no': []})
    max_pre_existing_n = 0
    pre_existing_completed_ns = set() 

    # Helper to process files from a given base directory and structure
    def _process_files_from_dir(base_dir, is_pre_existing=False):
        nonlocal max_pre_existing_n
        
        # Map n to a list of its part file paths, sorted by part number
        n_to_file_paths = defaultdict(list)

        if base_dir == ROOT_OUTPUT_BASE_DIR:
            # Look for n{n} subdirectories and their base/part files
            for n_dir_name in os.listdir(base_dir):
                if not n_dir_name.startswith('n') or not n_dir_name[1:].isdigit():
                    continue
                current_order = int(n_dir_name[1:])
                output_dir = os.path.join(base_dir, n_dir_name)
                
                # Check for base file (tournaments_n_X.txt)
                base_file = os.path.join(output_dir, f"tournaments_n_{current_order}{FILE_EXTENSION}")
                if os.path.exists(base_file):
                    n_to_file_paths[current_order].append((0, base_file)) # 0 for base file
                    
                # Check for part files (tournaments_n_X_partY.txt)
                for filename in os.listdir(output_dir):
                    match = re.match(rf'tournaments_n_{current_order}_part(\d+){re.escape(FILE_EXTENSION)}$', filename)
                    if match:
                        part_num = int(match.group(1))
                        n_to_file_paths[current_order].append((part_num, os.path.join(output_dir, filename)))
        
        elif base_dir == PRE_EXISTING_RESULTS_DIR and os.path.exists(base_dir):
            # Look for tournaments_n_X.txt or tournaments_n_X_partY.txt directly in the folder
            for filename in os.listdir(base_dir):
                base_match = re.match(r'tournaments_n_(\d+)\.txt$', filename)
                part_match = re.match(r'tournaments_n_(\d+)_part(\d+)\.txt$', filename)

                if base_match:
                    current_order = int(base_match.group(1))
                    n_to_file_paths[current_order].append((0, os.path.join(base_dir, filename)))
                    max_pre_existing_n = max(max_pre_existing_n, current_order) 

                elif part_match:
                    current_order = int(part_match.group(1))
                    part_num = int(part_match.group(2))
                    n_to_file_paths[current_order].append((part_num, os.path.join(base_dir, filename)))
                    max_pre_existing_n = max(max_pre_existing_n, current_order)
        
        # Now process the collected file paths for each n
        for current_order, paths_with_parts in n_to_file_paths.items():
            # Sort by part number to read in correct order (0 for base, then 1, 2, ...)
            sorted_paths = sorted(paths_with_parts, key=lambda x: x[0])
            
            all_lines_for_n = []
            found_any_part = False
            for part_num, filepath in sorted_paths:
                if os.path.exists(filepath):
                    found_any_part = True
                    try:
                        with open(filepath, 'r') as f:
                            all_lines_for_n.extend(f.readlines())
                    except Exception as e:
                        print(f"Error reading {filepath}: {e}")
            
            if found_any_part:
                found_orders.add(current_order)
                _parse_lines_into_results(all_lines_for_n, current_order, results)
                
                if is_pre_existing: # If data came from PRE_EXISTING_RESULTS_DIR
                    # Mark as completed if not currently in progress and not already completed by current run
                    if current_order not in overall_results_summary or \
                       overall_results_summary[current_order].get('completed', False) or \
                       not overall_results_summary[current_order].get('status', '').startswith('In Progress'):
                        pre_existing_completed_ns.add(current_order)


    _process_files_from_dir(ROOT_OUTPUT_BASE_DIR, is_pre_existing=False)
    _process_files_from_dir(PRE_EXISTING_RESULTS_DIR, is_pre_existing=True)

    return results, found_orders, max_pre_existing_n, pre_existing_completed_ns

def results_to_md(results, found_orders, current_progress_info, pre_existing_completed_ns):
    lines = []
    if not found_orders:
        return "No results found."

    min_order = min(found_orders)
    max_order = max(found_orders)

    lines.append("# Cospectral vs Switching Equivalence Results\n")
    lines.append("| n | Status | cospectral ⇒ switching |")
    lines.append("|---|--------|-------------------------|")

    for n in range(min_order, max_order + 1):
        has_results_for_n = n in results and (results[n]['yes'] or results[n]['no'])
        
        is_completed_by_current_run = current_progress_info.get(n, {}).get('completed', False)
        is_completed_by_pre_existing = n in pre_existing_completed_ns 

        is_completed = is_completed_by_current_run or is_completed_by_pre_existing

        if not has_results_for_n and not is_completed:
            status_text = "❓ No results"
            summary_text = "-"
        elif is_completed:
            yes = len(results[n]['yes'])
            no = len(results[n]['no'])
            total = yes + no
            percent_yes = (yes / total) * 100 if total > 0 else 0
            summary_text = f"{yes}/{total} ({percent_yes:.2f}%)"

            if not results[n]['no']:
                status_text = "✅ YES"
            else:
                status_text = "❌ NO"
        else: # Has results but not marked complete (i.e., actively in progress for this 'n')
            yes = len(results[n]['yes'])
            no = len(results[n]['no'])
            total = yes + no
            percent_yes = (yes / total) * 100 if total > 0 else 0
            summary_text = f"{yes}/{total} ({percent_yes:.2f}%)"
            status_text = "⏳ In Progress" # This is correct for active progress.

        lines.append(f"| {n} | {status_text} | {summary_text} |")

    lines.append("\n---\n")

    if currently_processing_n is not None:
        progress_n = currently_processing_n
        
        with overall_results_lock:
            active_n_info = overall_results_summary.get(progress_n)
        
        if active_n_info and not active_n_info.get('completed', True):
            lines.append(f"## 📊 Current Progress (Order n = {progress_n})\n")
            
            if active_n_info.get('current_status_message'):
                lines.append(f"> {active_n_info['current_status_message']}\n")

            total_expected_tournaments = NON_ISO_TOURNAMENTS.get(progress_n, 0)
            
            generated = active_n_info.get('current_progress_generated_tournaments', 0)
            if total_expected_tournaments > 0:
                gen_percent = (generated / total_expected_tournaments)
                gen_bar = generate_progress_bar(gen_percent, width=30)
                lines.append(f"Tournaments Generated: `{gen_bar}` ({generated}/{total_expected_tournaments} - {gen_percent*100:.2f}%)")
            elif generated > 0:
                 lines.append(f"Tournaments Generated: {generated} (Total for n={progress_n} unknown)")
            else:
                 lines.append("Tournaments Generation: Not started yet.")
            lines.append("\n")

            checked_classes = active_n_info.get('current_progress_checked_classes', 0)
            total_classes_found = active_n_info.get('total_classes', 0)
            
            if total_classes_found > 0:
                checked_percent = (checked_classes / total_classes_found)
                check_bar = generate_progress_bar(checked_percent, width=30)
                lines.append(f"Classes Checked: `{check_bar}` ({checked_classes}/{total_classes_found} - {checked_percent*100:.2f}%)")
                lines.append(f"  (✅ Yes: {active_n_info['yes_classes']}, ❌ No: {active_n_info['no_classes']})")
            elif checked_classes > 0:
                 lines.append(f"Classes Checked: {checked_classes} (Total classes for n={progress_n} unknown yet)")
            else:
                lines.append("Classes Checked: Not started yet.")
            lines.append("\n")
    lines.append("\n---\n")

    for n in range(min_order, max_order + 1):
        lines.append(f"## n = {n}")
        has_results_for_n = n in results and (results[n]['yes'] or results[n]['no'])
        
        is_completed_by_current_run = current_progress_info.get(n, {}).get('completed', False)
        is_completed_by_pre_existing = n in pre_existing_completed_ns 

        is_completed = is_completed_by_current_run or is_completed_by_pre_existing

        if not has_results_for_n and not is_completed:
            lines.append("> ❓ **No results for this order yet.**\n")
        elif not is_completed:
            lines.append(f"> ⏳ **Processing in progress for order n = {n}.**\n")
        elif not results[n]['no']:
            lines.append("> ✅ **cospectral ⇒ switching equivalent**\n")
        else:
            lines.append("> ❌ **cospectral ⇏ switching equivalence**\n")
            lines.append("### Characteristic Polynomial(s) (Non-switching-equivalent classes):")
            
            polys = results[n]['no']
            
            total_no_polys = len(polys)
            poly_count = 0
            subsection_idx = 0

            while poly_count < total_no_polys:
                subsection_idx += 1
                subsection_title = f"Part {subsection_idx} (Polys {poly_count + 1} - {min(poly_count + MAX_ALIGN_ENVS_PER_SUBSECTION * MAX_POLY_ROWS_PER_ALIGN_ENV, total_no_polys)})"
                
                lines.append(f"<details><summary>Click to expand for {subsection_title}</summary>\n")
                lines.append("\n")

                aligned_block_count = 0
                while aligned_block_count < MAX_ALIGN_ENVS_PER_SUBSECTION and poly_count < total_no_polys:
                    
                    current_aligned_polys = []
                    for _ in range(MAX_POLY_ROWS_PER_ALIGN_ENV):
                        if poly_count < total_no_polys:
                            current_aligned_polys.append(polys[poly_count])
                            poly_count += 1
                        else:
                            break

                    if current_aligned_polys:
                        max_deg = 0
                        split_current_polys = []

                        for poly in current_aligned_polys:
                            p = poly.replace(" ", "").replace("*", "")
                            terms = re.findall(r'[+-]?[^+-]+', p)
                            degs = []
                            for i in range(len(terms)):
                                t = terms[i]
                                m = re.search(r'x\^(\d+)', t)
                                if m:
                                    exp = m.group(1)
                                    t = t.replace(f"x^{exp}", f"x^{{{exp}}}")
                                    deg = int(exp)
                                elif 'x' in t:
                                    deg = 1
                                else:
                                    deg = 0
                                terms[i] = t
                                degs.append(deg)
                            max_deg = max(max_deg, max(degs, default=0))
                            split_current_polys.append((terms, degs))
                        
                        aligned_rows = []
                        for terms, degs in split_current_polys:
                            row = [''] * (max_deg + 1)
                            for t, d in zip(terms, degs):
                                row[max_deg - d] = t
                            aligned_rows.append(row)
                        
                        lines.append("$$")
                        lines.append("\\begin{aligned}")
                        for row in aligned_rows:
                            lines.append("  & " + " & ".join(t if t else "" for t in row) + " \\\\")
                        lines.append("\\end{aligned}")
                        lines.append("$$")
                        lines.append("\n")
                        aligned_block_count += 1
                
                lines.append("</details>\n")
                lines.append("\n")

        lines.append("")

        if not is_completed:
            lines.append(f"> ⚠️ _Note: Results for order n = {n} may be incomplete or analysis is ongoing._\n")
        
    return "\n".join(lines)


def hash_readme_content(md):
    return hashlib.sha256(md.encode('utf-8')).hexdigest()

# ===================== Periodic README Updater Thread =====================
readme_timer = None
stop_readme_event = threading.Event()
last_readme_hash = None 

def run_readme_updater(interval_seconds):
    global readme_timer
    global last_readme_hash

    if not stop_readme_event.is_set():
        results, found_orders, _, pre_existing_completed_ns = collect_results_for_readme() 
        
        with overall_results_lock:
            current_progress_info_copy = dict(overall_results_summary)

        md_body = results_to_md(results, found_orders, current_progress_info_copy, pre_existing_completed_ns) 
        current_hash = hash_readme_content(md_body)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        header = []
        if last_readme_hash is None:
            header.append(f"## 🚨 Initial README generated! (last change: {now})")
            last_readme_hash = current_hash
        elif current_hash != last_readme_hash:
            header.append(f"## 🚨 New results found! (last change: {now})")
            last_readme_hash = current_hash
        else:
            header.append(f"## No new results. (last change: {now})")
        header.append(f"_Last checked: {now}_\n")

        with open(README_PATH, "w") as f:
            f.write("\n".join(header) + "\n" + md_body)
        
        print(f"\n[README] README.md updated at {now}")
        
        readme_timer = threading.Timer(interval_seconds, run_readme_updater, args=[interval_seconds])
        readme_timer.daemon = True
        readme_timer.start()

def start_readme_updater(interval_seconds=60):
    run_readme_updater(interval_seconds) 

def stop_readme_updater():
    stop_readme_event.set()
    if readme_timer:
        readme_timer.cancel()
        print("[README] README updater thread requested to stop.")

# ===================== Main Execution =====================
if __name__ == "__main__":
    start_readme_updater(interval_seconds=60) # Keeping this for progress visibility, but you can comment it out if preferred.

    # Determine starting N for computation
    _, _, max_n_from_pre_existing, _ = collect_results_for_readme() 
    start_n_computation = max(3, max_n_from_pre_existing + 1)
    print(f"Starting computation from order n = {start_n_computation} (max pre-existing n: {max_n_from_pre_existing})")

    try:
        for n in range(start_n_computation, 19):
            run_sequential(n)
        
        print("\nAll computations completed. Performing final README update...")
        time.sleep(2) 
        run_readme_updater(0) 

    finally:
        stop_readme_updater()
        print("\nScript finished. README updater stopped.")