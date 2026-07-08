#!/usr/bin/env python3
"""brainmaster — memory custodian for Dimitri ecosystem.

Tasks:
  clean         — archive stale/duplicate/noise entries from MEMORY.md
  backup        — snapshot memory files to sysbackup
  status        — report memory health and trends
  lint          — syntax audit active memory files
  search <q>    — search active and cold memories for keyword
  consolidate   — find and group overlapping/similar memory entries
"""

import os
import re
import sys
import shutil
import datetime
from pathlib import Path

MEM_DIR = Path(os.environ.get('HERMES_MEM_DIR', '/home/angkolj/.hermes/profiles/dimitri/memories'))
MEM_FILE = MEM_DIR / 'MEMORY.md'
USER_FILE = MEM_DIR / 'USER.md'
COLD_DIR = Path(os.environ.get('BRAINMASTER_COLD_DIR', '/home/angkolj/.hermes/profiles/brainmaster/cold'))
SYSBACKUP = Path(os.environ.get('BRAINMASTER_BACKUP_DIR', '/media/angkolj/lynux-backup/brainmaster'))
LOCAL_BACKUP = Path(os.environ.get('BRAINMASTER_LOCAL_BACKUP', '/home/angkolj/.hermes/backup/brainmaster'))
STALE_DAYS = int(os.environ.get('BRAINMASTER_STALE_DAYS', '14'))


def get_backup_target():
    """Return SYSBACKUP if mounted, fallback to LOCAL_BACKUP if not."""
    if os.path.ismount(str(SYSBACKUP)) or SYSBACKUP.exists():
        return SYSBACKUP
    print(f"[WARN] Backup drive not mounted at {SYSBACKUP}. Using local fallback.")
    LOCAL_BACKUP.mkdir(parents=True, exist_ok=True)
    return LOCAL_BACKUP


def parse_entries(text):
    """Split memory text into entries (separated by §)."""
    # Normalize potential carriage returns and split by section separator
    entries = [e.strip() for e in text.replace('\r\n', '\n').split('§') if e.strip()]
    return entries


def classify_entry(entry):
    """Return (type, age_days, is_stale)."""
    # LESSON entries
    m = re.search(r'LESSON\s*\((\d{4}-\d{2}-\d{2})\)', entry)
    if m:
        dt = datetime.datetime.strptime(m.group(1), '%Y-%m-%d').date()
        age = (datetime.date.today() - dt).days
        return ('lesson', age, age > STALE_DAYS)
    
    # J-correction entries
    m = re.search(r'J preference|J corrected|J explicit|J strict|J wants|J said|J taught|Lesson\s+\d{4}-\d{2}-\d{2}', entry)
    if m and re.search(r'\(\d{4}-\d{2}-\d{2}\)', entry):
        m2 = re.search(r'\((\d{4}-\d{2}-\d{2})\)', entry)
        if m2:
            dt = datetime.datetime.strptime(m2.group(1), '%Y-%m-%d').date()
            age = (datetime.date.today() - dt).days
            return ('j-correction', age, age > STALE_DAYS * 2)
    
    # Large entries (potential bloat)
    if len(entry) > 500:
        return ('large', 0, False)
    
    return ('other', 0, False)


def get_tokens(text):
    """Extract clean lowercase tokens from entry text, ignoring header boilerplate."""
    # Strip dates and headers
    cleaned = re.sub(
        r'^(LESSON|J preference|J corrected|J explicit|J strict|J wants|J said|J taught|Lesson)\s*\(?\d{4}-\d{2}-\d{2}\)?:?\s*',
        '',
        text,
        flags=re.IGNORECASE
    )
    # Get alphanumeric words
    return set(re.findall(r'\w+', cleaned.lower()))


def jaccard_similarity(s1, s2):
    """Calculate token-based Jaccard similarity between two entries."""
    t1 = get_tokens(s1)
    t2 = get_tokens(s2)
    if not t1 or not t2:
        return 0.0
    return len(t1.intersection(t2)) / len(t1.union(t2))


def cmd_clean():
    """Archive stale lessons, remove duplicates using Jaccard Similarity, and compact memory."""
    if not MEM_FILE.exists():
        print(f"ERROR: MEMORY.md not found at {MEM_FILE}")
        return 1
    
    text = MEM_FILE.read_text(encoding='utf-8')
    entries = parse_entries(text)
    
    total = len(entries)
    lessons = []
    current = []
    stale = []
    
    for e in entries:
        typ, age, is_stale = classify_entry(e)
        if typ == 'lesson' and is_stale:
            stale.append(e)
        elif typ == 'lesson':
            lessons.append(e)
        else:
            current.append(e)
    
    # Write cold storage archive
    if stale:
        COLD_DIR.mkdir(parents=True, exist_ok=True)
        cold_file = COLD_DIR / f'cold-{datetime.date.today().isoformat()}.md'
        # Append or write fresh cold storage
        existing_cold_content = ""
        if cold_file.exists():
            existing_cold_content = cold_file.read_text(encoding='utf-8').strip()
        
        new_cold_entries = stale
        if existing_cold_content:
            existing_cold_entries = parse_entries(existing_cold_content)
            new_cold_entries = list(set(existing_cold_entries + stale))
            
        cold_file.write_text("§\n\n".join(new_cold_entries) + "\n§\n", encoding='utf-8')
        print(f"archived {len(stale)} stale lessons → {cold_file}")
    
    # Smart deduplication: keep lessons + current, drop high Jaccard overlap (>0.78)
    # Prefer preserving newer entries (those appearing later in the file)
    active = lessons + current
    deduped = []
    # Reverse to process newest first, so we keep the newest, then reverse back
    for e in reversed(active):
        is_dup = False
        for existing in deduped:
            sim = jaccard_similarity(e, existing)
            if sim > 0.78:
                is_dup = True
                break
            if e[:60] == existing[:60]:
                is_dup = True
                break
        if not is_dup:
            deduped.insert(0, e)
            
    # Write back
    MEM_FILE.write_text("§\n\n".join(deduped) + "\n§\n", encoding='utf-8')
    
    archived = len(stale)
    dupes = total - len(deduped) - archived
    print(f"clean: {total}→{len(deduped)} entries ({archived} archived, {dupes} dupes removed)")
    return 0


def cmd_backup():
    """Snapshot memory files to sysbackup."""
    target = get_backup_target()
    target.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    
    for src in [MEM_FILE, USER_FILE]:
        if src.exists():
            dst = target / f"{src.name}.{ts}"
            shutil.copy2(src, dst)
            print(f"backup: {src.name} → {dst} ({src.stat().st_size / 1024:.0f}K)")
    
    # Rotate: keep last 7 backups per file
    for suffix in ['MEMORY.md.*', 'USER.md.*']:
        backups = sorted(target.glob(suffix), reverse=True)
        for old in backups[7:]:
            old.unlink()
            print(f"rotated: removed {old.name}")
    
    return 0


def check_stale_locks():
    """Check for lock files older than 24h."""
    found = False
    for lock in MEM_DIR.glob('*.lock'):
        age = (datetime.datetime.now() - datetime.datetime.fromtimestamp(lock.stat().st_mtime)).total_seconds() / 3600
        if age > 24:
            print(f"  [WARN] Stale lock: {lock.name} ({age:.0f}h old)")
            found = True
    return found


def detect_contradictions(entries):
    """Find entries with high similarity but opposite intent."""
    contradictions = []
    negations = {'not','dont',"don't",'disable','never','avoid','stop','off','no','without'}
    for i, e1 in enumerate(entries):
        for e2 in entries[i+1:]:
            sim = jaccard_similarity(e1, e2)
            if sim > 0.45:
                t1 = get_tokens(e1)
                t2 = get_tokens(e2)
                has_neg1 = bool(t1 & negations)
                has_neg2 = bool(t2 & negations)
                if has_neg1 != has_neg2:
                    contradictions.append((e1[:80], e2[:80]))
                    break
    return contradictions


def cmd_status():
    """Report memory health and size trends."""
    if not MEM_FILE.exists():
        print("STATUS: MEMORY.md not found")
        return 1
    
    text = MEM_FILE.read_text(encoding='utf-8')
    entries = parse_entries(text)
    
    lessons = sum(1 for e in entries if classify_entry(e)[0] == 'lesson')
    stale_lessons = sum(1 for e in entries if classify_entry(e)[2])
    large = sum(1 for e in entries if classify_entry(e)[0] == 'large')
    size_kb = MEM_FILE.stat().st_size / 1024
    user_size_kb = USER_FILE.stat().st_size / 1024 if USER_FILE.exists() else 0
    
    cold_count = sum(1 for _ in COLD_DIR.glob('cold-*.md')) if COLD_DIR.exists() else 0
    target = get_backup_target()
    backup_count = sum(1 for _ in target.glob('MEMORY.md.*')) if target.exists() else 0
    
    print(f"=== Brainmaster Memory Status ===")
    print(f"Active Memory (MEMORY.md): {size_kb:.1f} KB, {len(entries)} total entries")
    print(f"  - Lessons: {lessons} ({stale_lessons} stale/ripe for archiving)")
    print(f"  - Large entries (>500 chars): {large}")
    print(f"User Prefs (USER.md):      {user_size_kb:.1f} KB")
    print(f"Cold Storage:             {cold_count} archives in {COLD_DIR}")
    print(f"Snapshots (Backup):       {backup_count} backups in {SYSBACKUP}")
    
    # Simple growth check (compare with oldest backup if available)
    backups = sorted(target.glob('MEMORY.md.*'))
    if backups:
        oldest_size = backups[0].stat().st_size / 1024
        diff = size_kb - oldest_size
        print(f"Growth trend (vs oldest snapshot): {diff:+.1f} KB")
    
    # Stale lock check
    check_stale_locks()
    
    # Contradiction check
    contradictions = detect_contradictions(entries)
    if contradictions:
        print(f"\n⚠ Potential contradictions ({len(contradictions)} found):")
        for e1, e2 in contradictions[:5]:
            print(f"  - \"{e1}...\"")
            print(f"    vs \"{e2}...\"")
    
    return 0


def cmd_lint():
    """Syntax audit active memory files for formatting errors."""
    has_issues = False
    for filepath in [MEM_FILE, USER_FILE]:
        if not filepath.exists():
            continue
        
        print(f"Linting {filepath.name}...")
        content = filepath.read_text(encoding='utf-8')
        
        # Check 1: Missing section separators (checks if entries are correctly separated)
        # Ensure section boundaries don't contain merged lines without '§'
        entries = parse_entries(content)
        for i, entry in enumerate(entries):
            # Check for lack of expected date structure in lessons
            if entry.startswith('LESSON') and not re.match(r'^LESSON\s*\(\d{4}-\d{2}-\d{2}\)', entry):
                print(f"  [WARN] Entry {i+1} has malformed LESSON header or date format.")
                has_issues = True
            
            # Check for empty lines or weird carriage returns inside the entry
            if '\n\n\n' in entry:
                print(f"  [INFO] Entry {i+1} contains excessive blank lines.")
        
        # Check 2: Raw check for double separators or trailing whitespace
        if '§§' in content:
            print("  [ERROR] File contains consecutive section characters (§§).")
            has_issues = True
            
        if content.strip() and not content.strip().endswith('§'):
            print("  [ERROR] File does not terminate with a section character (§).")
            has_issues = True
            
    if not has_issues:
        print("Linting passed. Memory syntax is clean.")
        return 0
    return 1


def cmd_search(query):
    """Search active and cold storage files for a specific query."""
    print(f"Searching memory for: '{query}'")
    results = []
    
    # 1. Search Active Memory
    if MEM_FILE.exists():
        entries = parse_entries(MEM_FILE.read_text(encoding='utf-8'))
        for e in entries:
            if query.lower() in e.lower():
                results.append(('Active Memory', e))
                
    # 2. Search User Prefs
    if USER_FILE.exists():
        entries = parse_entries(USER_FILE.read_text(encoding='utf-8'))
        for e in entries:
            if query.lower() in e.lower():
                results.append(('User Prefs', e))
                
    # 3. Search Cold Storage
    if COLD_DIR.exists():
        for cold_file in COLD_DIR.glob('cold-*.md'):
            entries = parse_entries(cold_file.read_text(encoding='utf-8'))
            for e in entries:
                if query.lower() in e.lower():
                    results.append((f"Cold Storage ({cold_file.name})", e))
                    
    # Print results
    if not results:
        print("No matching entries found.")
        return 0
        
    print(f"Found {len(results)} matches:")
    for src, entry in results:
        first_line = entry.split('\n')[0][:100]
        print(f"\n[{src}] {first_line}...")
        print(f"  {entry}")
    return 0


def cmd_consolidate():
    """Group similar entries in active memory (Jaccard similarity > 0.55) to suggest consolidation."""
    if not MEM_FILE.exists():
        print("MEMORY.md not found")
        return 1
        
    entries = parse_entries(MEM_FILE.read_text(encoding='utf-8'))
    print(f"Analyzing {len(entries)} entries for overlapping knowledge...")
    
    groups = []
    visited = set()
    
    for i, e1 in enumerate(entries):
        if i in visited:
            continue
            
        current_group = [i]
        for j, e2 in enumerate(entries):
            if i == j or j in visited:
                continue
                
            sim = jaccard_similarity(e1, e2)
            if sim > 0.55: # Suggest clusters that have moderate/high overlap
                current_group.append(j)
                visited.add(j)
                
        if len(current_group) > 1:
            groups.append(current_group)
            visited.add(i)
            
    if not groups:
        print("No significant memory overlap found.")
        return 0
        
    print(f"\nDetected {len(groups)} clusters of overlapping/similar memory:")
    for idx, group in enumerate(groups):
        print(f"\n--- Cluster #{idx+1} ---")
        for entry_idx in group:
            print(f"  * Entry {entry_idx+1}: {entries[entry_idx][:140]}...")
            
    print("\nTip: Consider merging these entries manually, or run 'brainmaster clean' to auto-prune near-exact duplicates.")
    return 0


def main():
    if len(sys.argv) < 2:
        print("Usage: brainmaster <clean|backup|status|lint|search|consolidate> [search_query]")
        return 1
    
    cmd = sys.argv[1]
    if cmd == 'clean':
        return cmd_clean()
    elif cmd == 'backup':
        return cmd_backup()
    elif cmd == 'status':
        return cmd_status()
    elif cmd == 'lint':
        return cmd_lint()
    elif cmd == 'search':
        if len(sys.argv) < 3:
            print("ERROR: Please specify a search query. e.g. brainmaster search Fortress")
            return 1
        return cmd_search(" ".join(sys.argv[2:]))
    elif cmd == 'consolidate':
        return cmd_consolidate()
    else:
        print(f"Unknown command: {cmd}")
        print("Supported commands: clean, backup, status, lint, search, consolidate")
        return 1


if __name__ == '__main__':
    sys.exit(main())
