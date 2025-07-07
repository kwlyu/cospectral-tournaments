import os
import re
import time
import json
from collections import defaultdict
from datetime import datetime, timedelta # Import timedelta
import threading
import hashlib # For calculating content hash

def format_timedelta(td):
    """Formats a timedelta object as a human-readable string."""
    total_seconds = int(td.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)

# ===================== Constants and Paths (Duplicated for Independence) =====================
README_PATH = os.path.join('/Users/lyuk/Downloads/cospectral-tournaments', "README.md")
ROOT_OUTPUT_BASE_DIR = "tournament_outputs_by_class"
FILE_EXTENSION = ".txt"
PRE_EXISTING_RESULTS_DIR = "NO" # Set to "NO" as requested
README_STATE_FILE = os.path.join(ROOT_OUTPUT_BASE_DIR, "readme_state.json") # New state file

# Checkpoint file names (must match tournament_runner.py)
GENERATION_CHECKPOINT_FILE_NAME = "generation_checkpoint.json"
CHECKING_CHECKPOINT_FILE_NAME = "checking_checkpoint.json"

# Data for number of non-isomorphic tournaments on n nodes (must match tournament_runner.py)
NON_ISO_TOURNAMENTS = {
    1: 1, 2: 1, 3: 2, 4: 4, 5: 12, 6: 56, 7: 456, 8: 6880,
    9: 191536, 10: 9733056, 11: 903753248, 12: 154108311168,
    13: 48542114686912, 14: 28401423719122304, 15: 31021002160355166848,
    16: 63530415842308265100288, 17: 244912778438520759443245823,
    18: 1783398846284777975419600287232
}

# README Formatting Constants
MAX_POLYS_PER_GATHER_BLOCK = 10
MAX_ALIGN_ENVS_PER_SUBSECTION = 10 # Set to 10 as requested

# ===================== Regex Patterns for README Parsing =====================
charpoly_pattern = re.compile(r'Characteristic Polynomial:\s*(.+)')
switching_yes_pattern = re.compile(r'All tournaments in this class are mutually switching equivalent')
trivial_switching_pattern = re.compile(r'Only one tournament - trivially switching equivalent')
switching_no_pattern = re.compile(r'Not all tournaments in this class are switching equivalent')
not_equiv_pattern = re.compile(r'Tournaments in this class are NOT all mutually switching equivalent')


# ===================== Helper Functions for README Generation =====================
def generate_progress_bar(percent: float, width: int = 30, style='blocks'):
    filled = int(percent * width)
    empty = width - filled
    if style == 'emoji':
        return '🟩' * filled + '⬜' * empty
    elif style == 'blocks':
        return '█' * filled + '░' * empty
    else:
        return '⬛' * filled + '⬜' * empty

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

def calculate_results_hash(results):
    """
    Calculates a hash based on the unique characteristic polynomials found.
    This hash changes only when new charpolys are appended.
    """
    canonical_string_parts = []
    for n_order in sorted(results.keys()):
        # Ensure consistent order for hashing
        all_polys_for_n = sorted(list(set(results[n_order]['yes'] + results[n_order]['no'])))
        canonical_string_parts.append(f"n{n_order}:" + ";".join(all_polys_for_n))
    
    full_canonical_string = "|".join(canonical_string_parts)
    return hashlib.md5(full_canonical_string.encode('utf-8')).hexdigest()

def load_readme_state():
    """Loads the last README hash and timestamps from state file."""
    if os.path.exists(README_STATE_FILE):
        try:
            with open(README_STATE_FILE, 'r') as f:
                state = json.load(f)
                return {
                    'last_readme_hash': state.get('last_readme_hash'),
                    'last_checked_time': state.get('last_checked_time'),
                    'last_change_time': state.get('last_change_time') # New field
                }
        except json.JSONDecodeError:
            print(f"Warning: Could not decode {README_STATE_FILE}. Starting fresh.")
    return {'last_readme_hash': None, 'last_checked_time': None, 'last_change_time': None}

def save_readme_state(current_hash, current_checked_time, current_change_time):
    """Saves the current README hash and timestamps to state file."""
    state = {
        'last_readme_hash': current_hash,
        'last_checked_time': current_checked_time,
        'last_change_time': current_change_time # New field
    }
    with open(README_STATE_FILE, 'w') as f:
        json.dump(state, f)

def collect_all_data_for_readme():
    """
    Collects all results from output files and determines current progress/completion
    status by reading checkpoint files.
    """
    found_orders = set()
    results = defaultdict(lambda: {'yes': [], 'no': []})
    current_progress_info = defaultdict(dict) # To store status, progress counts etc.
    completed_ns_in_primary_output = set() # 'n' values fully completed in ROOT_OUTPUT_BASE_DIR

    # Process files from ROOT_OUTPUT_BASE_DIR
    if os.path.exists(ROOT_OUTPUT_BASE_DIR):
        for n_dir_name in os.listdir(ROOT_OUTPUT_BASE_DIR):
            if not n_dir_name.startswith('n') or not n_dir_name[1:].isdigit():
                continue
            current_order = int(n_dir_name[1:])
            found_orders.add(current_order)
            n_dir_path = os.path.join(ROOT_OUTPUT_BASE_DIR, n_dir_name)

            # Initialize progress info for this order
            current_progress_info[current_order] = {
                'completed': False,
                'status': 'PENDING',
                'yes_classes': 0, 'no_classes': 0, 'total_classes': 0,
                'current_progress_generated_tournaments': 0,
                'current_progress_checked_classes': 0,
                'current_status_message': "",
                'generation_start_time_epoch': None,
                'generation_last_update_time_epoch': None,
                'checking_start_time_epoch': None,
                'checking_last_update_time_epoch': None,
                'estimated_completion_time_gen': None,
                'estimated_completion_time_chk': None,
                'rate_gen_per_min': None,
                'rate_chk_per_min': None
            }

            # Read generation checkpoint first to get base progress and total classes
            generation_checkpoint_path = os.path.join(n_dir_path, GENERATION_CHECKPOINT_FILE_NAME)
            if os.path.exists(generation_checkpoint_path):
                try:
                    with open(generation_checkpoint_path, 'r') as f:
                        gen_chkpt_data = json.load(f)
                    total_gen = gen_chkpt_data.get("total_tournaments_generated", 0)
                    class_data = gen_chkpt_data.get("class_data_for_current_n", {})
                    total_classes = len(class_data) # Total classes found during generation

                    gen_start_epoch = gen_chkpt_data.get("start_time_epoch")
                    gen_last_update_epoch = gen_chkpt_data.get("last_update_time_epoch")

                    current_progress_info[current_order].update({
                        'current_progress_generated_tournaments': total_gen,
                        'total_classes': total_classes,
                        'generation_start_time_epoch': gen_start_epoch,
                        'generation_last_update_time_epoch': gen_last_update_epoch
                    })

                    expected_total_tournaments = NON_ISO_TOURNAMENTS.get(current_order, 0)
                    
                    # --- Calculate ETA for Generation ---
                    if gen_start_epoch and gen_last_update_epoch and gen_start_epoch != gen_last_update_epoch:
                        time_elapsed_gen = datetime.fromtimestamp(gen_last_update_epoch) - datetime.fromtimestamp(gen_start_epoch)
                        if time_elapsed_gen.total_seconds() > 0 and total_gen > 0:
                            rate_gen_per_sec = total_gen / time_elapsed_gen.total_seconds()
                            rate_gen_per_min = rate_gen_per_sec * 60
                            current_progress_info[current_order]['rate_gen_per_min'] = f"{rate_gen_per_min:.2f} tourns/min"

                            if expected_total_tournaments > 0 and total_gen < expected_total_tournaments:
                                remaining_to_generate = expected_total_tournaments - total_gen
                                if rate_gen_per_sec > 0:
                                    estimated_time_remaining_seconds = remaining_to_generate / rate_gen_per_sec
                                    current_progress_info[current_order]['estimated_completion_time_gen'] = format_timedelta(timedelta(seconds=estimated_time_remaining_seconds))


                    if expected_total_tournaments > 0 and total_gen >= expected_total_tournaments:
                        current_progress_info[current_order]['status'] = "GENERATION_ONLY"
                        current_progress_info[current_order]['current_status_message'] = "Generation complete. Ready for checking."
                    elif total_gen > 0:
                        current_progress_info[current_order]['status'] = "In Progress (Generation)"
                        gen_percent = (total_gen / expected_total_tournaments) * 100 if expected_total_tournaments > 0 else 0
                        current_progress_info[current_order]['current_status_message'] = \
                            f"Generated {total_gen}/{expected_total_tournaments} tournaments for n={current_order} ({gen_percent:.2f}%)."
                except json.JSONDecodeError:
                    print(f"Warning: Could not decode generation checkpoint for n={current_order}. Skipping.")


            # Check for CHECKING_COMPLETE status or in-progress checking
            checking_checkpoint_path = os.path.join(n_dir_path, CHECKING_CHECKPOINT_FILE_NAME)
            if os.path.exists(checking_checkpoint_path):
                try:
                    with open(checking_checkpoint_path, 'r') as f:
                        chkpt_data = json.load(f)
                    
                    checked_classes = chkpt_data.get("total_checked_classes", 0)
                    yes_classes = chkpt_data.get("total_yes_classes", 0)
                    no_classes = chkpt_data.get("total_no_classes", 0)

                    chk_start_epoch = chkpt_data.get("start_time_epoch")
                    chk_last_update_epoch = chkpt_data.get("last_update_time_epoch")

                    current_progress_info[current_order].update({
                        'current_progress_checked_classes': checked_classes,
                        'yes_classes': yes_classes,
                        'no_classes': no_classes,
                        'checking_start_time_epoch': chk_start_epoch,
                        'checking_last_update_time_epoch': chk_last_update_epoch
                    })

                    # --- Calculate ETA for Checking ---
                    if chk_start_epoch and chk_last_update_epoch and chk_start_epoch != chk_last_update_epoch:
                        time_elapsed_chk = datetime.fromtimestamp(chk_last_update_epoch) - datetime.fromtimestamp(chk_start_epoch)
                        total_classes_for_n = current_progress_info[current_order]['total_classes'] # From generation phase

                        if time_elapsed_chk.total_seconds() > 0 and checked_classes > 0 and total_classes_for_n > 0:
                            rate_chk_per_sec = checked_classes / time_elapsed_chk.total_seconds()
                            rate_chk_per_min = rate_chk_per_sec * 60
                            current_progress_info[current_order]['rate_chk_per_min'] = f"{rate_chk_per_min:.2f} classes/min"

                            if checked_classes < total_classes_for_n:
                                remaining_to_check = total_classes_for_n - checked_classes
                                if rate_chk_per_sec > 0:
                                    estimated_time_remaining_seconds = remaining_to_check / rate_chk_per_sec
                                    current_progress_info[current_order]['estimated_completion_time_chk'] = format_timedelta(timedelta(seconds=estimated_time_remaining_seconds))


                    if chkpt_data.get("last_processed_class_filename") == "COMPLETE":
                        completed_ns_in_primary_output.add(current_order)
                        current_progress_info[current_order].update({
                            'completed': True,
                            'status': "CHECKING_COMPLETE",
                            'current_status_message': "" # Clear message for completed tasks
                        })
                    else: # Still in progress of checking
                        current_progress_info[current_order]['status'] = "In Progress (Checking)"
                        check_percent = (checked_classes / current_progress_info[current_order]['total_classes']) * 100 \
                            if current_progress_info[current_order]['total_classes'] > 0 else 0
                        current_progress_info[current_order]['current_status_message'] = \
                            f"Checking classes for n={current_order} ({checked_classes}/{current_progress_info[current_order]['total_classes']} - {check_percent:.2f}%)."
                except json.JSONDecodeError:
                    print(f"Warning: Could not decode checking checkpoint for n={current_order}. Skipping.")


            # Collect results from the output files (tournaments_n_X_partY.txt) in ROOT_OUTPUT_BASE_DIR
            output_dir = os.path.join(ROOT_OUTPUT_BASE_DIR, n_dir_name)
            
            relevant_files = []
            base_file = os.path.join(output_dir, f"tournaments_n_{current_order}{FILE_EXTENSION}")
            if os.path.exists(base_file):
                relevant_files.append(base_file)
            
            if os.path.exists(output_dir): # Check if output_dir actually exists
                for filename in os.listdir(output_dir):
                    match = re.match(rf'tournaments_n_{current_order}_part(\d+){re.escape(FILE_EXTENSION)}$', filename)
                    if match:
                        relevant_files.append(os.path.join(output_dir, filename))
                
            relevant_files.sort()

            for filepath in relevant_files:
                try:
                    with open(filepath, 'r') as f:
                        _parse_lines_into_results(f.readlines(), current_order, results)
                except Exception as e:
                    print(f"Warning: Could not read/parse {filepath}: {e}")

    # Process files from PRE_EXISTING_RESULTS_DIR (if defined and different from ROOT_OUTPUT_BASE_DIR)
    if PRE_EXISTING_RESULTS_DIR != "NO" and os.path.exists(PRE_EXISTING_RESULTS_DIR) and PRE_EXISTING_RESULTS_DIR != ROOT_OUTPUT_BASE_DIR:
        for filename in os.listdir(PRE_EXISTING_RESULTS_DIR):
            match = re.match(rf'tournaments_n_(\d+)(_part\d+)?{re.escape(FILE_EXTENSION)}$', filename)
            if match:
                current_order = int(match.group(1))
                filepath = os.path.join(PRE_EXISTING_RESULTS_DIR, filename)

                # Only process if this 'n' hasn't been found/completed in the primary output dir
                if current_order in found_orders: 
                    continue

                found_orders.add(current_order) # Add to found orders for README display
                # For pre-existing, assume they are complete for README display unless primary output says otherwise
                if current_order not in completed_ns_in_primary_output:
                    completed_ns_in_primary_output.add(current_order) 
                    # Initialize full progress info for pre-existing as if it was completed
                    current_progress_info[current_order] = {
                        'completed': True,
                        'status': "CHECKING_COMPLETE", 
                        'current_status_message': "Loaded from pre-existing results.",
                        'yes_classes': 0, 'no_classes': 0, 'total_classes': 0,
                        'current_progress_generated_tournaments': NON_ISO_TOURNAMENTS.get(current_order, 0),
                        'current_progress_checked_classes': 0, # Will be set by _parse_lines_into_results
                        'generation_start_time_epoch': None, 'generation_last_update_time_epoch': None,
                        'checking_start_time_epoch': None, 'checking_last_update_time_epoch': None,
                        'estimated_completion_time_gen': "N/A", 'estimated_completion_time_chk': "N/A",
                        'rate_gen_per_min': "N/A", 'rate_chk_per_min': "N/A"
                    }

                try:
                    with open(filepath, 'r') as f:
                        file_lines = f.readlines()
                        _parse_lines_into_results(file_lines, current_order, results)
                        # Update counts for pre-existing from parsed results
                        current_progress_info[current_order]['yes_classes'] = len(results[current_order]['yes'])
                        current_progress_info[current_order]['no_classes'] = len(results[current_order]['no'])
                        current_progress_info[current_order]['total_classes'] = len(results[current_order]['yes']) + len(results[current_order]['no'])
                        current_progress_info[current_order]['current_progress_checked_classes'] = current_progress_info[current_order]['total_classes']

                except Exception as e:
                    print(f"Warning: Could not read/parse {filepath} from pre-existing dir: {e}")

    return results, found_orders, current_progress_info, completed_ns_in_primary_output

def render_current_progress_section(current_progress_info):
    """
    Generates the Markdown for the "Current Progress" section based on the
    current_progress_info dictionary.
    """
    lines = []
    
    # Find the smallest 'n' that is currently in progress
    active_n = None
    for n in sorted(current_progress_info.keys()):
        info = current_progress_info[n]
        if not info.get('completed', False) and (
            info.get('status') == "In Progress (Generation)" or
            info.get('status') == "In Progress (Checking)" or
            info.get('status') == "GENERATION_ONLY" # Consider generation_only as active until checking starts
        ):
            active_n = n
            break

    if active_n is not None:
        active_n_info = current_progress_info[active_n]

        lines.append("\n---\n")
        lines.append(f"## 📊 Current Progress (Order n = {active_n})\n")
        
        if active_n_info.get('current_status_message'):
            lines.append(f"> {active_n_info['current_status_message']}\n")

        total_expected_tournaments = NON_ISO_TOURNAMENTS.get(active_n, 0)
        
        generated = active_n_info.get('current_progress_generated_tournaments', 0)
        if total_expected_tournaments > 0:
            gen_percent = (generated / total_expected_tournaments)
            gen_bar = generate_progress_bar(gen_percent, width=30)
            lines.append(f"Tournaments Generated: `{gen_bar}` ({generated}/{total_expected_tournaments} - {gen_percent*100:.2f}%)")
            
            # Display Generation ETA and Rate
            if active_n_info['estimated_completion_time_gen']:
                lines.append(f"  Estimated Completion (Generation): {active_n_info['estimated_completion_time_gen']}")
            if active_n_info['rate_gen_per_min']:
                lines.append(f"  Rate (Generation): {active_n_info['rate_gen_per_min']}")
        elif generated > 0:
            lines.append(f"Tournaments Generated: {generated} (Total for n={active_n} unknown)")
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
            
            # Display Checking ETA and Rate
            if active_n_info['estimated_completion_time_chk']:
                lines.append(f"  Estimated Completion (Checking): {active_n_info['estimated_completion_time_chk']}")
            if active_n_info['rate_chk_per_min']:
                lines.append(f"  Rate (Checking): {active_n_info['rate_chk_per_min']}")
        elif checked_classes > 0:
            lines.append(f"Classes Checked: {checked_classes} (Total classes for n={active_n} unknown yet)")
        else:
            lines.append("Classes Checked: Not started yet.")
        lines.append("\n")
        lines.append("---\n")

    return lines


def results_to_md(results, found_orders, current_progress_info, completed_ns_in_primary_output, header_lines):
    lines = []
    
    # Add the header lines at the very beginning
    lines.extend(header_lines)

    if not found_orders:
        lines.append("No results found.")
        # Still render progress section even if no completed orders
        lines.extend(render_current_progress_section(current_progress_info))
        return "\n".join(lines)

    min_order = min(found_orders)
    max_order = max(found_orders)

    lines.append("# Cospectral vs Switching Equivalence Results\n")
    lines.append("| n | Status | cospectral ⇒ switching |")
    lines.append("|---|--------|-------------------------|")

    for n in range(min_order, max_order + 1):
        has_results_for_n = n in results and (results[n]['yes'] or results[n]['no'])
        
        is_completed = current_progress_info.get(n, {}).get('completed', False)

        if not has_results_for_n and not is_completed:
            status_text = "❓ No results"
            summary_text = "-"
        else:
            yes = len(results[n]['yes'])
            no = len(results[n]['no'])
            total = yes + no
            percent_yes = (yes / total) * 100 if total > 0 else 0
            summary_text = f"{yes}/{total} ({percent_yes:.2f}%)"

            current_n_status = current_progress_info.get(n, {}).get('status')

            if current_n_status == "GENERATION_ONLY":
                status_text = "✅ GEN ONLY"
            elif current_n_status == "CHECKING_COMPLETE":
                if not results[n]['no']:
                    status_text = "✅ YES"
                else:
                    status_text = "❌ NO"
            elif current_n_status == "CHECKING_SKIPPED":
                status_text = "⚠️ CHECK SKIPPED"
            elif current_n_status is None or current_n_status == "PENDING":
                status_text = "❓ PENDING / Not Started"
            elif current_n_status.startswith("In Progress"):
                status_text = f"⏳ {current_n_status.replace('In Progress (', '').replace(')', '')}" # Shorten status for table
                
                gen_prog = current_progress_info.get(n, {}).get('current_progress_generated_tournaments', 0)
                check_prog = current_progress_info.get(n, {}).get('current_progress_checked_classes', 0)
                total_classes = current_progress_info.get(n, {}).get('total_classes', 0)
                expected_total_tournaments = NON_ISO_TOURNAMENTS.get(n, 0)
                
                if current_n_status == "In Progress (Generation)" and expected_total_tournaments > 0:
                    gen_percent = (gen_prog / expected_total_tournaments) if total_expected_tournaments > 0 else 0
                    status_text += f" ({gen_percent*100:.0f}%)" # Show % in status
                elif current_n_status == "In Progress (Checking)" and total_classes > 0:
                    check_percent = (check_prog / total_classes) if total_classes > 0 else 0
                    status_text += f" ({check_percent*100:.0f}%)" # Show % in status
            else:
                status_text = f"❓ Unknown Status"
            
        lines.append(f"| {n} | {status_text} | {summary_text} |")
    
    # Render current progress section after the main table
    lines.extend(render_current_progress_section(current_progress_info))

    # Detailed results section
    lines.append("\n## Detailed Results\n")
    for n in sorted(found_orders):
        if n in results and results[n]['no']: # Only include if there are 'no' classes
            lines.append(f"<details><summary>Order n = {n}</summary>\n")
            
            # No Classes
            if results[n]['no']:
                lines.append(f"### Cospectral BUT NOT Switching Equivalent (NO classes: {len(results[n]['no'])})\n")
                no_polys = sorted(results[n]['no']) # Sort for consistent output

                # Gather blocks
                for i in range(0, len(no_polys), MAX_POLYS_PER_GATHER_BLOCK):
                    current_block_polys = no_polys[i:i + MAX_POLYS_PER_GATHER_BLOCK]
                    lines.append("$$ \\begin{gather*}")
                    # Use {poly} for x^10 to display properly
                    lines.extend([f"{poly} \\\\" for poly in current_block_polys])
                    lines.append("\\end{gather*} $$")
                    lines.append("\n") # Blank line for readability

            lines.append("</details>\n")

    return "\n".join(lines)

def update_readme_main():
    """
    Main function to collect data and update the README.md file.
    This function is designed to be run as a standalone script or periodically.
    """
    try:
        results, found_orders, current_progress_info, completed_ns_in_primary_output = collect_all_data_for_readme()
        
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Load previous state
        state_data = load_readme_state()
        last_readme_hash_from_state = state_data.get('last_readme_hash')
        last_change_time_from_state = state_data.get('last_change_time') 
        
        current_hash = calculate_results_hash(results)

        header_lines = []
        
        # Determine the hash and change time to save for the next run
        next_last_readme_hash_to_save = current_hash
        next_last_checked_time_to_save = now
        
        # Initialize next_last_change_time_to_save.
        # This ensures it always has a valid timestamp for saving,
        # either from a loaded state or current 'now' if first run/old format.
        next_last_change_time_to_save = last_change_time_from_state if last_change_time_from_state else now 

        # This will be the time actually displayed in the "last change" part of the header
        display_change_time = next_last_change_time_to_save 

        if last_readme_hash_from_state is None:
            # First run, or state file was completely missing/corrupt. Treat as initial generation.
            header_lines.append(f"## 🚨 Initial README generated! (last change: {now})")
            # For the first run, the 'last change' *is* now.
            next_last_change_time_to_save = now 
        elif current_hash != last_readme_hash_from_state:
            # New results found (hash changed).
            header_lines.append(f"## 🚨 New results found! (last change: {now})")
            # New results mean the 'last change' time should be updated to now.
            next_last_change_time_to_save = now 
        else:
            # No new results. The display_change_time already holds the persisted value
            # from next_last_change_time_to_save's initialization.
            header_lines.append(f"## No new results. (last change: {display_change_time})")
            # next_last_change_time_to_save retains its initial assignment (the loaded persisted value)

        header_lines.append(f"_Last checked: {now}_\n")

        md_content = results_to_md(results, found_orders, current_progress_info, completed_ns_in_primary_output, header_lines)
        
        with open(README_PATH, 'w') as f:
            f.write(md_content)
        print(f"[README] README.md updated at {now}")

        # Save the updated state for the next run
        save_readme_state(next_last_readme_hash_to_save, next_last_checked_time_to_save, next_last_change_time_to_save)

    except Exception as e:
        print(f"Error during README.md update process: {e}")

def run_periodic_readme_update():
    """Runs the README update periodically."""
    update_readme_main()
    # Set the interval for periodic updates (e.g., every 60 seconds)
    readme_updater_interval = 60 
    readme_timer = threading.Timer(readme_updater_interval, run_periodic_readme_update)
    readme_timer.daemon = True # Allow program to exit even if main thread exits
    readme_timer.start()

if __name__ == "__main__":
    # Start the periodic README update
    run_periodic_readme_update()
    # Keep the script alive so the daemon thread can run
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nREADME generator stopped.")