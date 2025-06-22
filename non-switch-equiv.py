import os
import re
import time
import hashlib
from collections import defaultdict
from datetime import datetime

OUTPUT_DIR = "/Users/lyuk/Downloads/cospectral-tournaments/tournament_outputs"
FILE_EXTENSION = ".txt"
README_PATH = os.path.join("/Users/lyuk/Downloads/cospectral-tournaments", "README.md")

order_pattern = re.compile(r'Order n = (\d+)')
filename_order_pattern = re.compile(r'_n_(\d+)(?:_part\d+)?\.txt$')
charpoly_pattern = re.compile(r'Characteristic Polynomial:\s*(.+)')
switching_yes_pattern = re.compile(r'All tournaments in this class are mutually switching equivalent')
trivial_switching_pattern = re.compile(r'Only one tournament - trivially switching equivalent')
switching_no_pattern = re.compile(r'Not all tournaments are switching equivalent')
not_equiv_pattern = re.compile(r'Tournaments \d+ and \d+ are NOT switching equivalent')

def collect_results():
    """
    Collects results from all .txt files in OUTPUT_DIR, parsing for
    tournament order, characteristic polynomials, and switching equivalence status.
    Returns a dictionary of results grouped by order and a set of found orders.
    """
    found_orders = set()
    for filename in os.listdir(OUTPUT_DIR):
        if not filename.endswith(FILE_EXTENSION):
            continue
        name_order_match = filename_order_pattern.search(filename)
        if name_order_match:
            found_orders.add(int(name_order_match.group(1)))

    results = defaultdict(lambda: {'yes': [], 'no': []})

    for filename in os.listdir(OUTPUT_DIR):
        if not filename.endswith(FILE_EXTENSION):
            continue

        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath) as f:
            lines = f.readlines()

        current_order = None
        current_poly = None

        for line in lines:
            # Try to get order from filename first if not found yet
            if current_order is None:
                filename_order_match = filename_order_pattern.search(filename)
                if filename_order_match:
                    current_order = int(filename_order_match.group(1))

            poly_match = charpoly_pattern.search(line)
            if poly_match:
                current_poly = poly_match.group(1).strip()
                continue

            # Check for switching equivalence status and store polynomial
            if switching_yes_pattern.search(line) or trivial_switching_pattern.search(line):
                if current_order is not None and current_poly:
                    results[current_order]['yes'].append(current_poly)
                    current_poly = None # Reset for next class
                continue
            
            if switching_no_pattern.search(line) or not_equiv_pattern.search(line):
                if current_order is not None and current_poly:
                    results[current_order]['no'].append(current_poly)
                    current_poly = None # Reset for next class
                continue

    return results, found_orders

def results_to_md(results, found_orders):
    """
    Generates the Markdown content for the README.md file based on the collected results.
    """
    lines = []
    if not found_orders:
        return "No results found."

    min_order = min(found_orders)
    max_order = max(found_orders)

    lines.append("# Cospectral vs Switching Equivalence Results\n")
    lines.append("| n | Status |")
    lines.append("|---|--------|")

    # Table summary
    for n in range(min_order, max_order + 1):
        has_results_for_n = n in results and (results[n]['yes'] or results[n]['no'])
        
        # Determine if 'n' should be marked incomplete based on the absence of 'n+1'
        # This condition now correctly checks if (n+1) is NOT in found_orders,
        # without excluding the max_order. This means if 9 is the max, and 10 isn't there,
        # 9 will be marked incomplete.
        mark_n_incomplete = ((n + 1) not in found_orders)

        if not has_results_for_n:
            lines.append(f"| {n} | ❓ No results |")
        elif not results[n]['no']:
            status = "✅ YES"
            if mark_n_incomplete:
                status = "⚠️ Incomplete (✅ YES)"
            lines.append(f"| {n} | {status} |")
        else:
            status = "❌ NO"
            if mark_n_incomplete:
                status = "⚠️ Incomplete (❌ NO)"
            lines.append(f"| {n} | {status} |")

    lines.append("\n---\n")

    # Detailed section per n
    for n in range(min_order, max_order + 1):
        lines.append(f"## n = {n}")
        has_results_for_n = n in results and (results[n]['yes'] or results[n]['no'])
        # Use the same logic for marking incomplete in the detailed section
        mark_n_incomplete = ((n + 1) not in found_orders)


        if not has_results_for_n:
            lines.append("> ❓ **No results for this order.**\n")
        elif not results[n]['no']:
            lines.append("> ✅ **cospectral ⇒ switching equivalent**\n")
        else:
            lines.append("> ❌ **cospectral ⇏ switching equivalence**\n")
            lines.append("### Characteristic Polynomial(s) (Non-switching-equivalent classes):")
            
            polys = results[n]['no']
            split_polys = []
            max_deg = 0
            
            # Process each polynomial to find terms and max degree for alignment
            for poly in polys:
                p = poly.replace(" ", "").replace("*", "") # Remove spaces and asterisks
                terms = re.findall(r'[+-]?[^+-]+', p) # Split into terms (e.g., "x^8", "-2x^6", "+1")
                degs = []
                for t in terms:
                    m = re.match(r'.*x\^(\d+)', t)
                    if m:
                        degs.append(int(m.group(1)))
                    elif 'x' in t: # Handle x^1
                        degs.append(1)
                    else: # Handle constant terms
                        degs.append(0)
                max_deg = max(max_deg, max(degs, default=0))
                split_polys.append((terms, degs))
            
            # Align terms for LaTeX output
            aligned_rows = []
            for terms, degs in split_polys:
                row = [''] * (max_deg + 1) # Initialize row with empty strings
                for t, d in zip(terms, degs):
                    row[max_deg - d] = t # Place term at correct index (higher degree terms first)
                aligned_rows.append(row)
            
            # Generate LaTeX aligned environment
            lines.append("$$\n\\begin{aligned}")
            for row in aligned_rows:
                lines.append("  & " + " & ".join(t if t else "" for t in row) + " \\\\")
            lines.append("\\end{aligned}\n$$")
        lines.append("")  # Blank line for spacing

        # Add the specific "next order missing" note here
        if mark_n_incomplete:
            lines.append(f"> ⚠️ _Note: Results for order n = {n+1} are not yet available. This suggests the data for n = {n} may not be complete or the analysis is ongoing._\n")
        elif not has_results_for_n:
            # If no results at all for 'n', still mention it specifically
            lines.append(f"> ❓ _Note: No output files were found for order n = {n}. The results for this order are missing._\n")

    return "\n".join(lines)


def hash_results(md):
    """
    Computes a SHA256 hash of the Markdown content to detect changes.
    """
    return hashlib.sha256(md.encode('utf-8')).hexdigest()

def main_loop():
    """
    Main loop to periodically check for new results and update the README.
    """
    last_hash = None
    last_change_time = None

    while True:
        results, found_orders = collect_results()
        md_body = results_to_md(results, found_orders)
        current_hash = hash_results(md_body)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if last_hash is None:
            # First run
            changed = True
            last_change_time = now
        elif current_hash != last_hash:
            # Content has changed
            changed = True
            last_change_time = now
        else:
            # No change
            changed = False

        # Prepare header for README
        header = []
        if changed:
            header.append(f"## 🚨 New results found! (last change: {last_change_time})")
        else:
            header.append(f"## No new results. (last change: {last_change_time})")
        header.append(f"_Last checked: {now}_\n")

        # Write to README.md
        with open(README_PATH, "w") as f:
            f.write("\n".join(header) + "\n" + md_body)

        last_hash = current_hash
        time.sleep(60) # Wait for 60 seconds before checking again

if __name__ == "__main__":
    main_loop()
