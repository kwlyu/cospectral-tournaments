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
            if current_order is None:
                filename_order_match = filename_order_pattern.search(filename)
                if filename_order_match:
                    current_order = int(filename_order_match.group(1))

            poly_match = charpoly_pattern.search(line)
            if poly_match:
                current_poly = poly_match.group(1).strip()
                continue

            if switching_yes_pattern.search(line):
                if current_order and current_poly:
                    results[current_order]['yes'].append(current_poly)
                    current_poly = None
                continue
            
            if trivial_switching_pattern.search(line):
                if current_order and current_poly:
                    results[current_order]['yes'].append(current_poly)
                    current_poly = None
                continue

            if switching_no_pattern.search(line):
                if current_order and current_poly:
                    results[current_order]['no'].append(current_poly)
                    current_poly = None
                continue

            if not_equiv_pattern.search(line):
                if current_order and current_poly:
                    results[current_order]['no'].append(current_poly)
                    current_poly = None
                continue

    return results, found_orders

def results_to_md(results, found_orders):
    lines = []
    if not found_orders:
        return "No results found."

    min_order = min(found_orders)
    max_order = max(found_orders)

    lines.append("# Cospectral vs Switching Equivalence Results\n")
    lines.append("| n | Status |")
    lines.append("|---|--------|")

    # Table summary
    for n in range(1, max_order + 1):
        if n not in results or (not results.get(n, {}).get('yes') and not results.get(n, {}).get('no')):
            lines.append(f"| {n} | ❓ No results |")
        elif not results[n]['no']:
            lines.append(f"| {n} | ✅ YES |")
        else:
            lines.append(f"| {n} | ❌ NO |")

    lines.append("\n---\n")

    # Detailed section per n
    for n in range(1, max_order + 1):
        lines.append(f"## n = {n}")
        if n not in results or (not results.get(n, {}).get('yes') and not results.get(n, {}).get('no')):
            lines.append("> ❓ **No results for this order.**\n")
        elif not results[n]['no']:
            lines.append("> ✅ **cospectral ⇒ switching equivalent**\n")
        else:
            lines.append("> ❌ **cospectral ⇏ switching equivalence**\n")
            lines.append("### Characteristic Polynomial(s) (Non-switching-equivalent classes):")
            # Align all polynomials by padding terms
            polys = results[n]['no']
            # Split polynomials into terms and find max degree
            split_polys = []
            max_deg = 0
            for poly in polys:
                # Remove spaces and asterisks for easier splitting
                p = poly.replace(" ", "").replace("*", "")
                # Split into terms (assume form like x^8-2x^6+...)
                terms = re.findall(r'[+-]?[^+-]+', p)
                degs = []
                for t in terms:
                    m = re.match(r'.*x\^(\d+)', t)
                    if m:
                        degs.append(int(m.group(1)))
                    elif 'x' in t:
                        degs.append(1)
                    else:
                        degs.append(0)
                max_deg = max(max_deg, max(degs, default=0))
                split_polys.append((terms, degs))
            # Build aligned rows
            aligned_rows = []
            for terms, degs in split_polys:
                row = [''] * (max_deg + 1)
                for t, d in zip(terms, degs):
                    row[max_deg - d] = t
                aligned_rows.append(row)
            # Output as LaTeX aligned environment
            lines.append("$$\n\\begin{aligned}")
            for row in aligned_rows:
                lines.append("  & " + " & ".join(t if t else "\\phantom{0}" for t in row) + " \\\\")
            lines.append("\\end{aligned}\n$$")
        lines.append("")  # Blank line for spacing

        if n + 1 not in found_orders and n != max_order:
            lines.append(f"> ⚠️ _Incomplete: No output found for n = {n + 1} (so order {n} may not be fully covered)_\n")

    return "\n".join(lines)


def hash_results(md):
    return hashlib.sha256(md.encode('utf-8')).hexdigest()

def main_loop():
    last_hash = None
    last_change_time = None

    while True:
        results, found_orders = collect_results()
        md_body = results_to_md(results, found_orders)
        current_hash = hash_results(md_body)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if last_hash is None:
            changed = True
            last_change_time = now
        elif current_hash != last_hash:
            changed = True
            last_change_time = now
        else:
            changed = False

        header = []
        if changed:
            header.append(f"## 🚨 New results found! (last change: {last_change_time})")
        else:
            header.append(f"## No new results. (last change: {last_change_time})")
        header.append(f"_Last checked: {now}_\n")

        with open(README_PATH, "w") as f:
            f.write("\n".join(header) + "\n" + md_body)

        last_hash = current_hash
        time.sleep(60)

if __name__ == "__main__":
    main_loop()