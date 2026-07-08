#!/usr/bin/env python3
"""
brain.py — Unified Brain API (Sprint 1)
Single cognitive interface: query, learn, health, benchmark, governance.

Usage:
  brain query "question"              # Unified retrieval across all stores
  brain learn "observation"            # Same-session learning (immediate spool+promote)
  brain health                         # Brain Health Report
  brain benchmark                      # Retrieval latency measurement
  brain dedup                          # Find duplicates across stores
  brain prune-user                     # Trim old USER.md entries
  brain db-archive                     # Archive sessions >90d to file
  brain graph-rebuild                  # Rebuild knowledge graph
  brain hub-bridge "topic" "content"   # Push entry to cross-profile hub
"""
from __future__ import annotations
import json, math, os, re, sys, glob, time, shutil, sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional

# ponytail: reuse existing paths and loaders from knowledge-cli.py
GROW_DIR = Path.home() / ".hermes" / "grow"
VAULT_DIR = Path.home() / "HERMES_WORKSPACE" / "OPENCODEX_VAULT"
PROFILE_DIR = Path.home() / ".hermes" / "profiles" / "dimitri"
PROMOTED_DIR = GROW_DIR / "promoted"
RULES_DIR = GROW_DIR / "rules" / "active"
LESSONS_DIR = VAULT_DIR / "Knowledge-Lessons"
GRAPH_FILE = GROW_DIR / "knowledge-graph.json"
SPOOL_DIR = GROW_DIR / "spool" / "dimitri"
MEMORY_FILE = PROFILE_DIR / "memories" / "MEMORY.md"
USER_FILE = PROFILE_DIR / "memories" / "USER.md"
SESSION_DB = PROFILE_DIR / "state.db"
HUB_DIR = GROW_DIR / "hub"

STOP_WORDS = set("the a an and or but in on at to for of with by from as is are was were be been have has had do does did will would shall should may might must can could".split())

# ── Memory Governance ─────────────────────────────────────────

@dataclass
class MemoryEntry:
    """Every memory object carries this metadata."""
    content: str
    status: str = "derived"           # canonical | derived | temporary | archived
    owner: str = "dimitri"
    source: str = ""                  # memory | grow | vault | session | user | skill
    confidence: float = 0.5           # 0.0-1.0
    freshness: str = ""               # ISO date
    last_used: str = ""               # ISO date
    usage_count: int = 0
    version: int = 1
    store: str = ""                   # which store it lives in
    storage_path: str = ""            # file path
    claim_id: str = ""                # unique identifier for dedup


def tokenize(text):
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s\-]', ' ', text)
    return [t for t in text.split() if len(t) > 2 and t not in STOP_WORDS]


def jaccard(a: str, b: str) -> float:
    """Fast overlap check for dedup. ponytail: O(n) set, good enough for text <500 chars."""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ── Retrieval Router ──────────────────────────────────────────

class RetrievalRouter:
    """Decides which stores to search based on query type.
    ponytail: keyword-classified, no ML. Add patterns if queries miss."""
    
    QUERY_TYPES = {
        'technical': ['tool', 'command', 'python', 'bash', 'api', 'config', 'how to', 'install', 'error', 'fix', 'bug'],
        'preference': ['prefer', 'like', 'want', 'dont', 'never', 'always', 'j said', 'rule'],
        'memory': ['remember', 'we did', 'session', 'last time', 'recall', 'what is', 'who is', 'where is'],
        'planning': ['plan', 'roadmap', 'sprint', 'next', 'should i', 'proposal'],
        'learning': ['learned', 'lesson', 'mistake', 'correction', 'improve'],
    }
    
    @classmethod
    def classify(cls, query: str) -> str:
        q = query.lower()
        for qtype, keywords in cls.QUERY_TYPES.items():
            if any(kw in q for kw in keywords):
                return qtype
        return 'general'
    
    @classmethod
    def select_stores(cls, query: str) -> list[str]:
        """Return store names to search. ponytail: all stores for general, subset for specific types."""
        qtype = cls.classify(query)
        if qtype == 'memory':
            return ['memory', 'session', 'vault']
        if qtype == 'technical':
            return ['memory', 'grow', 'vault', 'skills']
        if qtype == 'preference':
            return ['user', 'memory', 'grow']
        if qtype == 'learning':
            return ['grow', 'vault', 'memory']
        return ['memory', 'user', 'grow', 'vault', 'session', 'skills']


# ── Brain Core ────────────────────────────────────────────────

class Brain:
    """Single cognitive interface. Departments call this, not individual stores."""

    @staticmethod
    def query(request: str, top_n: int = 5) -> dict:
        """Unified retrieval. Returns ranked results with source attribution."""
        results = []
        stores = RetrievalRouter.select_stores(request)
        query_tokens = tokenize(request)
        if not query_tokens:
            return {"query": request, "results": [], "stores_searched": stores}

        # ponytail: inline TF-IDF against each store instead of subprocess calls
        # Store loaders (inline, avoids importing knowledge-cli's module path)
        entries = []
        if 'memory' in stores:
            entries.extend(Brain._load_memory())
        if 'user' in stores:
            entries.extend(Brain._load_user())
        if 'grow' in stores:
            entries.extend(Brain._load_grow())
        if 'vault' in stores:
            entries.extend(Brain._load_vault_lessons())
        if 'skills' in stores:
            entries.extend(Brain._load_skills())

        if not entries:
            # fallback: knowledge-cli
            import subprocess
            r = subprocess.run(
                [sys.executable, str(GROW_DIR / "scripts" / "knowledge-cli.py"), request, "--top", str(top_n)],
                capture_output=True, text=True, timeout=15
            )
            return {"query": request, "results": [{"source": "knowledge-cli", "output": r.stdout[:2000]}],
                    "stores_searched": stores, "fallback": True}

        # ponytail: global IDF across all loaded entries
        doc_count = len(entries)
        doc_freq = Counter()
        for e in entries:
            for t in set(e.get('tokens', [])):
                doc_freq[t] += 1
        idf_lookup = {t: math.log(doc_count / max(f, 1)) + 1 for t, f in doc_freq.items()} if doc_count else {}

        # Score each entry
        for e in entries:
            score = 0.0
            toks = e.get('tokens', [])
            if toks:
                qf = Counter(query_tokens)
                df = Counter(toks)
                for token, freq in qf.items():
                    if token in df:
                        tf = df[token] / len(toks)
                        idf = idf_lookup.get(token, 1.0)
                        score += freq * tf * max(idf, 0.3)
            # Boost rules
            if e.get('type') == 'rule':
                score *= 1.3
            # Recency boost
            if e.get('freshness'):
                try:
                    age_days = (datetime.now() - datetime.fromisoformat(e['freshness'])).days
                    score *= math.exp(-0.693 * age_days / 14)
                except:
                    pass
            if score > 0:
                results.append({"score": round(score, 4), **e})

        results.sort(key=lambda x: x['score'], reverse=True)
        return {
            "query": request,
            "results": results[:top_n],
            "stores_searched": stores,
            "total_scored": len(results)
        }

    # ── Store Loaders (inline, ponytail: reuse patterns from knowledge-cli) ──

    @staticmethod
    def _load_memory():
        entries = []
        if MEMORY_FILE.exists():
            content = MEMORY_FILE.read_text()
            facts = [f.strip() for f in content.split('§') if f.strip()]
            for fact in facts:
                first = fact.split('\n')[0][:80]
                entries.append({
                    'source': 'memory', 'store': 'persistent_memory',
                    'title': first, 'content': fact[:300],
                    'type': 'fact', 'freshness': '', 'tokens': tokenize(fact[:500])
                })
        return entries

    @staticmethod
    def _load_user():
        entries = []
        if USER_FILE.exists():
            content = USER_FILE.read_text()
            facts = [f.strip() for f in content.split('§') if f.strip()]
            for fact in facts:
                first = fact.split('\n')[0][:80]
                entries.append({
                    'source': 'user', 'store': 'user_profile',
                    'title': first, 'content': fact[:300],
                    'type': 'preference', 'freshness': '', 'tokens': tokenize(fact[:500])
                })
        return entries

    @staticmethod
    def _load_grow():
        entries = []
        # Promoted lessons
        for f in sorted(glob.glob(str(PROMOTED_DIR / "prom-*.md"))):
            content = open(f).read()
            meta = {}
            for line in content.split('\n'):
                if ':' in line and not line.startswith('#'):
                    k, v = line.split(':', 1)
                    meta[k.strip()] = v.strip().strip('"')
            if meta.get('trigger') or meta.get('claim'):
                text = f"{meta.get('trigger', '')} {meta.get('behavior', '')} {meta.get('claim', '')}"
                entries.append({
                    'source': 'grow', 'store': 'grow_promoted',
                    'title': meta.get('trigger', meta.get('claim', ''))[:80],
                    'content': meta.get('behavior', meta.get('claim', '')),
                    'type': 'lesson', 'freshness': meta.get('promoted_at', ''),
                    'tokens': tokenize(text),
                    'category': meta.get('category', ''),
                    'confidence': float(meta.get('confidence', 0.5)) if meta.get('confidence') else 0.5,
                })
        # Rules
        for f in sorted(glob.glob(str(RULES_DIR / "rule-*.md"))):
            content = open(f).read()
            meta = {}
            for line in content.split('\n'):
                if ':' in line and not line.startswith('#'):
                    k, v = line.split(':', 1)
                    meta[k.strip()] = v.strip().strip('"')
            if meta.get('trigger'):
                entries.append({
                    'source': 'grow', 'store': 'grow_rule',
                    'title': meta.get('trigger', '')[:80],
                    'content': meta.get('behavior', ''),
                    'type': 'rule', 'freshness': meta.get('promoted_at', ''),
                    'tokens': tokenize(meta.get('trigger', '') + ' ' + meta.get('behavior', '')),
                    'confidence': 0.8 if meta.get('confidence') == 'high' else 0.5,
                })
        return entries

    @staticmethod
    def _load_vault_lessons():
        entries = []
        if not LESSONS_DIR.exists():
            return entries
        for f in sorted(glob.glob(str(LESSONS_DIR / "*.md"))):
            if 'INDEX' in f or '.bak' in f:
                continue
            content = open(f).read()
            title = os.path.basename(f).replace('.md', '')
            for line in content.split('\n'):
                if line.startswith('# '):
                    title = line[2:].strip()
                    break
            entries.append({
                'source': 'vault', 'store': 'knowledge_lessons',
                'title': title[:80], 'content': content[:300],
                'type': 'lesson', 'freshness': '',
                'tokens': tokenize(title + ' ' + content[:500])
            })
        return entries

    @staticmethod
    def _load_skills():
        """Load skill titles+descriptions. ponytail: only metadata, not full content."""
        entries = []
        skills_dir = PROFILE_DIR / "skills"
        if not skills_dir.exists():
            return entries
        for f in sorted(glob.glob(str(skills_dir / "**" / "SKILL.md"), recursive=True)):
            content = open(f).read()
            title = ""
            desc = ""
            for line in content.split('\n'):
                if line.startswith('name:') and not title:
                    title = line.split(':', 1)[1].strip().strip('"\'')
                elif line.startswith('description:') and not desc:
                    desc = line.split(':', 1)[1].strip().strip('"\'')
            if title:
                entries.append({
                    'source': 'skill', 'store': 'skills',
                    'title': title[:80], 'content': desc[:300],
                    'type': 'skill', 'freshness': '',
                    'tokens': tokenize(title + ' ' + desc)
                })
        return entries


    # ── Online Learning ──────────────────────────────────────────

    @staticmethod
    def learn(observation: str, category: str = "auto") -> dict:
        """Same-session learning: write to spool + promote immediately.
        ponytail: skips daily batch wait by writing to spool + promoted dir directly."""
        today = datetime.now().strftime('%Y-%m-%d')
        now_ts = datetime.now().isoformat()

        # 1. Write to spool
        SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        spool_file = SPOOL_DIR / f"{today}.ndjson"
        entry = {
            "ts": now_ts,
            "category": category,
            "claim": observation[:200],
            "trigger": f"When {observation[:100].lower()}",
            "behavior": observation[:300],
            "verify": "Check output against lesson before finishing",
            "evidence": f"Online-learned: {observation[:100]}",
            "source": "online-learning",
            "online_promoted": True,
        }
        with open(spool_file, 'a') as f:
            f.write(json.dumps(entry) + '\n')

        # 2. Promote immediately (skip daily pipeline wait)
        promoted = Brain._promote_immediate(entry)
        
        return {
            "learned": True,
            "observation": observation[:200],
            "spool_file": str(spool_file),
            "promoted": promoted,
            "available_now": True
        }

    @staticmethod
    def _promote_immediate(entry: dict) -> bool:
        """Write entry directly to promoted dir. ponytail: inline promotion, no dedup/validation loop."""
        PROMOTED_DIR.mkdir(parents=True, exist_ok=True)
        nx = 1
        for f in os.listdir(PROMOTED_DIR):
            if f.startswith('prom-') and f.endswith('.md'):
                try:
                    nx = max(nx, int(f.split('-')[-1].replace('.md', '')) + 1)
                except: pass
        claim = entry.get('claim', '')[:200]
        trigger = entry.get('trigger', claim)[:200]
        behavior = entry.get('behavior', claim)[:200]
        today = datetime.now().strftime('%Y-%m-%d')
        fn = f"prom-{today}-onl-{nx:03d}.md"

        def yaml_escape(s):
            return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')[:300]

        with open(PROMOTED_DIR / fn, 'w') as f:
            f.write(f'---\nid: prom-{today}-onl-{nx:03d}\ncategory: {entry.get("category", "auto")}\n'
                    f'claim: "{yaml_escape(claim)}"\ntrigger: "{yaml_escape(trigger)}"\n'
                    f'behavior: "{yaml_escape(behavior)}"\nverify: "Check output against lesson before finishing."\n'
                    f'evidence: "Online-learned: {yaml_escape(claim[:80])}"\n'
                    f'recurrence: 1\npromoted_at: {today}\nsource: online-learning\n---\n')
        return True

    # ── Health ───────────────────────────────────────────────────

    @staticmethod
    def health() -> dict:
        """Brain Health Report — all metrics."""
        metrics = {}
        
        # Memory usage
        mem_size = MEMORY_FILE.stat().st_size if MEMORY_FILE.exists() else 0
        mem_chars = len(MEMORY_FILE.read_text()) if MEMORY_FILE.exists() else 0
        user_size = USER_FILE.stat().st_size if USER_FILE.exists() else 0
        user_chars = len(USER_FILE.read_text()) if USER_FILE.exists() else 0
        metrics['persistent_memory'] = {
            'chars': mem_chars, 'limit': 93000, 'pct': round(mem_chars / 93000 * 100, 1),
            'entries': len([f for f in (MEMORY_FILE.read_text().split('§') if MEMORY_FILE.exists() else []) if f.strip()])
        }
        metrics['user_profile'] = {
            'chars': user_chars, 'limit': 8000, 'pct': round(user_chars / 8000 * 100, 1),
            'entries': len([f for f in (USER_FILE.read_text().split('§') if USER_FILE.exists() else []) if f.strip()])
        }

        # GROW
        promoted = len(glob.glob(str(PROMOTED_DIR / "prom-*.md")))
        rules = len(glob.glob(str(RULES_DIR / "rule-*.md")))
        spool_files = len(glob.glob(str(SPOOL_DIR / "*.ndjson"))) if SPOOL_DIR.exists() else 0
        metrics['grow'] = {'promoted': promoted, 'rules': rules, 'spool_files': spool_files}

        # Knowledge graph
        graph_nodes = graph_edges = 0
        if GRAPH_FILE.exists():
            try:
                g = json.loads(GRAPH_FILE.read_text())
                graph_nodes = len(g.get('nodes', {}))
                graph_edges = len(g.get('edges', []))
            except: pass
        metrics['knowledge_graph'] = {'nodes': graph_nodes, 'edges': graph_edges}

        # Vault lessons
        vault_lessons = len(glob.glob(str(LESSONS_DIR / "*.md"))) if LESSONS_DIR.exists() else 0
        metrics['vault'] = {'knowledge_lessons': vault_lessons}

        # Session DB
        db_size = 0
        db_sessions = 0
        db_messages = 0
        if SESSION_DB.exists():
            db_size = SESSION_DB.stat().st_size
            try:
                conn = sqlite3.connect(str(SESSION_DB))
                db_messages = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
                db_sessions = conn.execute('SELECT COUNT(DISTINCT session_id) FROM messages').fetchone()[0]
                conn.close()
            except: pass
        metrics['session_db'] = {
            'size_bytes': db_size, 'sessions': db_sessions, 'messages': db_messages
        }

        # Skills
        skill_count = len(glob.glob(str(PROFILE_DIR / "skills" / "**" / "SKILL.md"), recursive=True))
        metrics['skills'] = {'count': skill_count}

        # Hub
        hub_topics = 0
        hub_stale = 0
        hi = HUB_DIR / "hub-index.json"
        if hi.exists():
            try:
                h = json.loads(hi.read_text())
                hub_topics = h.get('card_count', 0)
                hub_stale = h.get('stale_count', 0)
            except: pass
        metrics['hub'] = {'topics': hub_topics, 'stale': hub_stale}

        return metrics

    @staticmethod
    def benchmark(iterations: int = 3) -> dict:
        """Measure retrieval latency for a standard query set."""
        queries = [
            "how to fix python error",
            "what is J's preference about reports",
            "medic monitor pipeline",
            "memory management lesson",
            "tool quirk cron"
        ]
        results = {}
        for q in queries:
            times = []
            for _ in range(iterations):
                t0 = time.time()
                Brain.query(q, top_n=3)
                times.append(time.time() - t0)
            results[q] = {
                'mean_ms': round(sum(times) / len(times) * 1000, 1),
                'min_ms': round(min(times) * 1000, 1),
                'max_ms': round(max(times) * 1000, 1),
            }
        return {
            'iterations': iterations,
            'queries': results,
            'overall_mean_ms': round(sum(r['mean_ms'] for r in results.values()) / len(results), 1)
        }


    # ── Dedup ────────────────────────────────────────────────────

    @staticmethod
    def dedup(threshold: float = 0.6) -> dict:
        """Find duplicate entries across all memory stores.
        ponytail: simple Jaccard overlap, no clustering. Reports duplicates but doesn't auto-remove."""
        all_entries = []
        
        # Load from memory
        if MEMORY_FILE.exists():
            content = MEMORY_FILE.read_text()
            facts = [f.strip() for f in content.split('§') if f.strip()]
            for i, fact in enumerate(facts):
                all_entries.append({
                    'id': f'memory:{i}', 'store': 'MEMORY.md', 'text': fact[:200],
                    'full_text': fact[:500]
                })

        # Load from promoted
        for f in sorted(glob.glob(str(PROMOTED_DIR / "prom-*.md"))):
            content = open(f).read()
            meta = {}
            for line in content.split('\n'):
                if line.startswith('claim:'):
                    meta['claim'] = line.split(':', 1)[1].strip().strip('"')
                    break
            if meta.get('claim'):
                all_entries.append({
                    'id': f'promoted:{os.path.basename(f)}', 'store': 'GROW promoted',
                    'text': meta['claim'][:200], 'full_text': meta['claim'][:500]
                })

        # Load from vault lessons
        if LESSONS_DIR.exists():
            for f in sorted(glob.glob(str(LESSONS_DIR / "*.md"))):
                if 'INDEX' in f:
                    continue
                content = open(f).read()
                title = os.path.basename(f).replace('.md', '')
                for line in content.split('\n'):
                    if line.startswith('# '):
                        title = line[2:].strip()
                        break
                all_entries.append({
                    'id': f'vault:{os.path.basename(f)}', 'store': 'Knowledge-Lessons',
                    'text': title[:200], 'full_text': title[:500]
                })

        # Compare all pairs. ponytail: O(n²) for n < 200 entries, fine at current scale.
        duplicates = []
        seen_pairs = set()
        for i in range(len(all_entries)):
            for j in range(i + 1, len(all_entries)):
                pair = (all_entries[i]['id'], all_entries[j]['id'])
                rev_pair = (all_entries[j]['id'], all_entries[i]['id'])
                if pair in seen_pairs or rev_pair in seen_pairs:
                    continue
                sim = jaccard(all_entries[i]['text'], all_entries[j]['text'])
                if sim >= threshold:
                    duplicates.append({
                        'a': {'id': all_entries[i]['id'], 'store': all_entries[i]['store'], 'text': all_entries[i]['text'][:80]},
                        'b': {'id': all_entries[j]['id'], 'store': all_entries[j]['store'], 'text': all_entries[j]['text'][:80]},
                        'similarity': round(sim, 3)
                    })
                    seen_pairs.add(pair)
                    seen_pairs.add(rev_pair)

        return {
            'total_entries_scanned': len(all_entries),
            'duplicates_found': len(duplicates),
            'threshold': threshold,
            'duplicates': sorted(duplicates, key=lambda x: -x['similarity']),
            'recommendation': 'Review flagged pairs. Only one canonical source per fact.'
        }


    # ── Ops: USER.md prune ───────────────────────────────────────

    @staticmethod
    def prune_user(target_pct: int = 70) -> dict:
        """Trim oldest USER.md entries if near capacity. ponytail: drops oldest entries first."""
        if not USER_FILE.exists():
            return {'error': 'USER.md not found'}
        content = USER_FILE.read_text()
        facts = [f.strip() for f in content.split('§') if f.strip()]
        current_chars = len(content)
        limit = 8000
        target_chars = int(limit * target_pct / 100)
        
        if current_chars <= target_chars:
            return {'pruned': 0, 'current_chars': current_chars, 'target_chars': target_chars, 'message': 'Below target, nothing to prune'}
        
        # Remove oldest entries until under target. ponytail: first entries are oldest.
        to_remove = 0
        while to_remove < len(facts):
            remaining_chars = sum(len(f) + 1 for f in facts[to_remove:])
            if remaining_chars <= target_chars:
                break
            to_remove += 1
        
        if to_remove == 0:
            return {'pruned': 0, 'message': 'Cannot prune, single entry too large'}
        
        kept = facts[to_remove:]
        USER_FILE.write_text('§\n'.join(kept) + '\n' if kept else '')
        return {
            'pruned': to_remove,
            'kept': len(kept),
            'chars_before': current_chars,
            'chars_after': sum(len(f) + 1 for f in kept),
            'pct_before': round(current_chars / limit * 100, 1),
            'pct_after': round(sum(len(f) + 1 for f in kept) / limit * 100, 1),
        }

    # ── Ops: Session DB archive ──────────────────────────────────

    @staticmethod
    def db_archive(days: int = 90) -> dict:
        """Archive sessions older than N days to a JSONL file. ponytail: one-time dump, no in-place delete."""
        if not SESSION_DB.exists():
            return {'error': 'Session DB not found'}
        
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()
        archive_dir = GROW_DIR / "session_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_file = archive_dir / f"sessions_pre_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        
        count = 0
        try:
            conn = sqlite3.connect(str(SESSION_DB))
            # Get old sessions
            old_sessions = conn.execute(
                "SELECT DISTINCT session_id FROM messages WHERE timestamp < ?", (cutoff,)
            ).fetchall()
            session_ids = [s[0] for s in old_sessions]
            
            with open(archive_file, 'w') as af:
                for sid in session_ids:
                    rows = conn.execute(
                        "SELECT id, session_id, role, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp",
                        (sid,)
                    ).fetchall()
                    for row in rows:
                        af.write(json.dumps({
                            'id': row[0], 'session_id': row[1], 'role': row[2],
                            'content': row[3][:500], 'timestamp': row[4]
                        }) + '\n')
                        count += 1
            conn.close()
        except Exception as e:
            return {'error': str(e)}
        
        return {
            'archived_to': str(archive_file),
            'sessions_archived': len(session_ids),
            'messages_archived': count,
            'older_than_days': days,
            'note': 'Read only — messages remain in live DB. Delete manually if needed.'
        }

    # ── Ops: Graph rebuild ─────────────────────────────────────

    @staticmethod
    def graph_rebuild() -> dict:
        """Rebuild knowledge graph from all lessons. ponytail: imports knowledge-engine's build_graph."""
        sys.path.insert(0, str(GROW_DIR / "scripts"))
        try:
            import knowledge_engine
            # ponytail: reload to pick up new lessons
            import importlib
            importlib.reload(knowledge_engine)
            graph = knowledge_engine.build_graph()
            return {
                'rebuilt': True,
                'nodes': len(graph.get('nodes', {})),
                'edges': len(graph.get('edges', [])),
                'updated': graph.get('updated', '')
            }
        except Exception as e:
            return {'error': str(e)}

    # ── Ops: Hub bridge ────────────────────────────────────────

    @staticmethod
    def hub_bridge(topic: str, content: str) -> dict:
        """Push an entry to cross-profile hub. ponytail: writes to staging, hub-promote handles promotion."""
        staging = HUB_DIR / "_staging"
        staging.mkdir(parents=True, exist_ok=True)
        fn = staging / f"{datetime.now().strftime('%Y-%m-%d')}-{topic.replace(' ', '-')[:40].lower()}.md"
        fn.write_text(f"# {topic}\n\n{content}\n\n---\nsubmitted_by: brain-sprint1\nsubmitted_at: {datetime.now().isoformat()}\n")
        return {
            'staged': True,
            'file': str(fn),
            'topic': topic,
            'next': 'Will be promoted on next hub-promote.sh run (part of daily pipeline)'
        }


    # ── Task Run (P3: Kernel authority) ─────────────────────────

    @staticmethod
    def task_run(name: str, command: str, timeout: int = 600) -> dict:
        """Run a shell command as a kernel-governed task.
        ponytail: creates kernel Task, runs subprocess, records outcome.
        Keeps rollback available — shell script runs regardless of kernel state."""
        import subprocess
        import uuid
        from datetime import datetime

        task_id = f"run-{uuid.uuid4().hex[:12]}"
        task = {
            "id": task_id, "name": name, "command": command,
            "start": datetime.now().isoformat(), "state": "running"
        }

        # ponytail: write start to tasks.jsonl
        tasks_jsonl = Path.home() / ".hermes" / "runtime" / "kernel" / "tasks.jsonl"
        tasks_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with open(tasks_jsonl, "a") as f:
            f.write(json.dumps(task) + "\n")

        try:
            r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
            task["state"] = "completed" if r.returncode == 0 else "failed"
            task["exit_code"] = r.returncode
            task["stdout"] = r.stdout[-500:]
            task["stderr"] = r.stderr[-500:]
            task["end"] = datetime.now().isoformat()
            task["duration_s"] = round(
                (datetime.fromisoformat(task["end"]) - datetime.fromisoformat(task["start"])).total_seconds(), 1
            )
        except subprocess.TimeoutExpired:
            task["state"] = "timed_out"
            task["end"] = datetime.now().isoformat()

        # Update task state in JSONL (append updated)
        with open(tasks_jsonl, "a") as f:
            f.write(json.dumps(task) + "\n")

        return {
            "task_id": task_id,
            "name": name,
            "state": task["state"],
            "exit_code": task.get("exit_code"),
            "duration_s": task.get("duration_s"),
            "stdout_preview": task.get("stdout", "")[:200],
            "stderr_preview": task.get("stderr", "")[:200],
        }


    # ── Telemetry ────────────────────────────────────────────────

    @staticmethod
    def telemetry() -> dict:
        """Read kernel audit/task JSONL, compute runtime aggregates.
        ponytail: reads JSONL directly, no streaming. Add live collector when latency matters."""
        audit_path = Path.home() / ".hermes" / "runtime" / "kernel" / "audit.jsonl"
        tasks_path = Path.home() / ".hermes" / "runtime" / "kernel" / "tasks.jsonl"

        # ponytail: safe reader, skip corrupt lines
        def read_jsonl(p):
            if not p.exists():
                return []
            out = []
            for line in p.read_text().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return out

        audit = read_jsonl(audit_path)
        tasks = read_jsonl(tasks_path)

        # ponytail: no live model latency yet — only tool audit records
        # Add model latency by hooking pre_llm_call in tool-audit plugin
        tool_calls = [e for e in audit if e.get("event_type") == "tool_call"]
        tool_names = Counter(e.get("action", "unknown") for e in tool_calls)
        task_by_state = Counter(str(t.get("state")) for t in tasks)
        task_by_owner = Counter(t.get("owner", "unknown") for t in tasks)

        return {
            "audit_total": len(audit),
            "tool_calls_total": len(tool_calls),
            "tool_calls_by_name": dict(tool_names.most_common(20)),
            "tasks_total": len(tasks),
            "tasks_by_state": dict(task_by_state),
            "tasks_by_owner": dict(task_by_owner),
            "tool_latency": "N/A — not captured yet (add pre/post timestamps to audit)",
            "model_latency": "N/A — no model audit hook yet",
            "estimated_cost": "N/A — no token tracking yet",
        }

    # ── Capability Execute ──────────────────────────────────────

    @staticmethod
    def execute(capability_id: str, request: dict = None) -> dict:
        """Execute a registered capability. ponytail: thin wrapper around kernel registry.
        Skips dependency resolution — add when workflows span multiple capabilities."""
        try:
            sys.path.insert(0, str(Path.home() / "HERMES_WORKSPACE" / "projects" / "hermes-kernel"))
            from kernel.capability import CapabilityRegistry
        except ImportError:
            return {"error": "Kernel not available", "capability_id": capability_id}

        persist_dir = str(Path.home() / ".hermes" / "runtime" / "kernel")
        reg = CapabilityRegistry(persist_path=f"{persist_dir}/capabilities.jsonl")

        cap = reg.get(capability_id)
        if not cap:
            return {"error": f"Capability '{capability_id}' not registered",
                    "registered": list(reg.list_all().keys())[:10]}

        return {
            "capability_id": cap.id,
            "name": cap.name,
            "owner": cap.owner,
            "status": cap.status.value,
            "request": request or {},
            "execution": "delegated to agent",
            "required_tools": cap.required_tools,
            "required_models": cap.required_models,
        }


# ── CLI ────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'query':
        if len(sys.argv) < 3:
            print("Usage: brain query \"your question\"")
            sys.exit(1)
        request = ' '.join(sys.argv[2:])
        result = Brain.query(request)
        print(json.dumps(result, indent=2))

    elif cmd == 'learn':
        if len(sys.argv) < 3:
            print("Usage: brain learn \"what you learned\"")
            sys.exit(1)
        observation = ' '.join(sys.argv[2:])
        result = Brain.learn(observation)
        print(json.dumps(result, indent=2))

    elif cmd == 'health':
        metrics = Brain.health()
        # Add runtime telemetry section
        metrics['runtime_telemetry'] = Brain.telemetry()
        print("# Brain Health Report")
        print(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print()
        for key, val in metrics.items():
            print(f"## {key}")
            if isinstance(val, dict):
                for k, v in val.items():
                    if isinstance(v, dict):
                        print(f"- {k}:")
                        for sk, sv in list(v.items())[:5]:
                            print(f"    {sk}: {sv}")
                    else:
                        print(f"- {k}: {v}")
            print()

    elif cmd == 'benchmark':
        result = Brain.benchmark()
        print("# Retrieval Benchmark")
        print(f"Iterations per query: {result['iterations']}")
        print()
        for q, times in result['queries'].items():
            print(f"Query: {q}")
            print(f"  Mean: {times['mean_ms']}ms  Min: {times['min_ms']}ms  Max: {times['max_ms']}ms")
        print(f"\nOverall mean: {result['overall_mean_ms']}ms")

    elif cmd == 'dedup':
        threshold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6
        result = Brain.dedup(threshold)
        print(f"# Duplicate Scan (threshold: {threshold})")
        print(f"\nEntries scanned: {result['total_entries_scanned']}")
        print(f"Duplicates found: {result['duplicates_found']}")
        print()
        for d in result['duplicates']:
            print(f"- [{d['a']['store']}] {d['a']['text'][:60]}")
            print(f"  [{d['b']['store']}] {d['b']['text'][:60]}")
            print(f"  similarity={d['similarity']}")
            print()
        if result.get('recommendation'):
            print(f"**Recommendation:** {result['recommendation']}")

    elif cmd == 'prune-user':
        target = int(sys.argv[2]) if len(sys.argv) > 2 else 70
        result = Brain.prune_user(target)
        print(json.dumps(result, indent=2))

    elif cmd == 'db-archive':
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        result = Brain.db_archive(days)
        print(json.dumps(result, indent=2))

    elif cmd == 'graph-rebuild':
        result = Brain.graph_rebuild()
        print(json.dumps(result, indent=2))

    elif cmd == 'hub-bridge':
        if len(sys.argv) < 4:
            print("Usage: brain hub-bridge \"topic\" \"content\"")
            sys.exit(1)
        topic = sys.argv[2]
        content = ' '.join(sys.argv[3:])
        result = Brain.hub_bridge(topic, content)
        print(json.dumps(result, indent=2))

    elif cmd == 'telemetry':
        result = Brain.telemetry()
        print("# Runtime Telemetry")

    elif cmd == 'task-run':
        if len(sys.argv) < 4:
            print("Usage: brain task-run <name> <command> [timeout]")
            sys.exit(1)
        name = sys.argv[2]
        cmd = sys.argv[3]
        timeout = int(sys.argv[4]) if len(sys.argv) > 4 else 600
        result = Brain.task_run(name, cmd, timeout)
        print(json.dumps(result, indent=2))

    elif cmd == 'execute':
        if len(sys.argv) < 3:
            print("Usage: brain execute <capability_id> [request_json]")
            sys.exit(1)
        cid = sys.argv[2]
        req = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        result = Brain.execute(cid, req)
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
