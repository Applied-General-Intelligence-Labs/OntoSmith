"""Neo4j implementations of EntityRegistry and GraphStore.

Phase 1: Neo4j implemented BOTH interfaces. That is superseded for
the registry role: the source of truth is now the file-backed
SqliteEntityRegistry (pipeline/file_registry.py), and Neo4j is the
graph projection. Neo4jEntityRegistry remains for legacy callers and
as the batch CLI's `JEV_REGISTRY=neo4j` fallback; new writes mint
unique IDs and honor corpus namespaces either way.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from pathlib import Path

from . import connections
from .registry import Entity, EntityRegistry, GraphStore, Triple, attestation_id


def _password() -> str:
    """Neo4j password from the environment only
    (secrets are never settings values or files in the tree).
    The variable is NEO4J_PASSWORD; empty string when unset."""
    return os.environ.get("NEO4J_PASSWORD", "")


class Neo4jClient:
    """HTTP client for Neo4j with two transports.

    - tx: the transactional endpoint (/db/<db>/tx/commit), used for the
      local server. One call carries many statements, committed atomically.
    - query: the Query API v2 (/db/<db>/query/v2), used for Aura — the
      egress proxy denies the tx path ("Denied by administrative rules").
      One statement per call, auto-committed; every caller statement is an
      independent MERGE, so no atomicity is lost in practice.

    Aura is selected automatically when the URL host is
    *.databases.neo4j.io; on Aura the database name and the username are
    both the instance id.
    """

    def __init__(self, url: str | None = None,
                 user: str | None = None, password: str | None = None,
                 db: str | None = None):
        import os
        url = url or os.environ.get("NEO4J_URL", "http://localhost:7474")
        user = user or os.environ.get("NEO4J_USER", "neo4j")
        self.transport = "query" if "databases.neo4j.io" in url else "tx"
        host_id = url.split("//")[-1].split(".")[0]
        self.db = db or (host_id if self.transport == "query" else "neo4j")
        base = url.rstrip("/")
        if self.transport == "query":
            self.url = f"{base}/db/{self.db}/query/v2"
        else:
            self.url = f"{base}/db/{self.db}/tx/commit"
        self.auth = base64.b64encode(
            f"{user}:{password if password is not None else _password()}"
            .encode()).decode()

    def _post(self, body: bytes, timeout: float = 60) -> dict:
        req = urllib.request.Request(
            self.url, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Basic {self.auth}"},
            method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())

    def run(self, statements: list[dict],
            timeout: float | None = None) -> list:
        kw = {} if timeout is None else {"timeout": timeout}
        if self.transport == "query":
            out = []
            for s in statements:
                data = self._post(json.dumps(
                    {"statement": s["statement"],
                     "parameters": s.get("parameters") or {}}).encode(),
                    **kw)
                if data.get("errors"):
                    raise RuntimeError(f"Neo4j errors: {data['errors'][:2]}")
                values = data.get("data", {}).get("values", [])
                out.append([{"row": v} for v in values])
            return out
        data = self._post(json.dumps({"statements": statements}).encode(),
                          **kw)
        if data.get("errors"):
            raise RuntimeError(f"Neo4j errors: {data['errors'][:2]}")
        return [s["data"] for s in data["results"]]

    def one(self, statement: str, parameters: dict | None = None):
        rows = self.run([{"statement": statement,
                          "parameters": parameters or {}}])[0]
        return rows[0]["row"][0] if rows else None


class StoreUnreachableError(RuntimeError):
    """The configured Neo4j store cannot serve a build —
    raised by the run preflight before any stage spends
   . The message names what is wrong and where the
    setup is documented; the CLI prints it as a one-line
    refusal, never a traceback."""


class StoreIdentityError(RuntimeError):
    """The store's current graph cannot be identified, or
    a write would replace a graph it cannot name — raised
    before anything is overwritten (store ruling,
    2026-10-23). Like StoreUnreachableError, the CLI
    prints it as a one-line refusal, never a traceback."""


# ------------------------------------------------------------------
# The store's graph stamp: the store holds
# exactly ONE graph — the materialization of the workspace's
# completed, current ontology. Every build/projection that
# writes the graph stamps the store with the graph's identity
# (which ontology built it, which batch, how much is in it).
# A later write under a different identity REPLACES the
# previous graph (naming it first); a store whose content no
# stamp identifies is never overwritten silently — the write
# stops by name instead. The registry keeps the full history;
# the store is current-state only.
# ------------------------------------------------------------------

#: The identity fields a graph stamp carries, in read order.
GRAPH_STAMP_FIELDS = (
    "batch_id", "run_id", "pack_id", "pack_version",
    "pack_payload_sha256", "profile_family", "profile_version",
    "profile_hash", "collection", "source", "triples",
    "entities", "chunks", "stamped_at")

_STAMP_COUNT_FIELDS = ("triples", "entities", "chunks")


def read_graph_stamp(client) -> dict | None:
    """The store's current graph stamp, or None when no
    build has stamped one. A stamp row whose values do not
    parse to the field shape reads as absent (the store is
    then judged by its content, never by a corrupt stamp)."""
    rows = client.run([{
        "statement": "MATCH (g:OntoSmithGraph) RETURN "
                     + ", ".join(f"g.{f}" for f in GRAPH_STAMP_FIELDS),
        "parameters": {}}])[0]
    if not rows:
        return None
    vals = rows[0]["row"]
    if len(vals) != len(GRAPH_STAMP_FIELDS):
        return None
    stamp = dict(zip(GRAPH_STAMP_FIELDS, vals, strict=True))
    for f in GRAPH_STAMP_FIELDS:
        if f in _STAMP_COUNT_FIELDS:
            stamp[f] = int(stamp[f] or 0)
        else:
            stamp[f] = stamp[f] or ""
    return stamp


def write_graph_stamp(client, stamp: dict) -> None:
    """(Over)write the store's graph stamp: the singleton
    (:OntoSmithGraph) node carrying the identity of the
    graph the store now holds."""
    params = {f: stamp.get(f, "") for f in GRAPH_STAMP_FIELDS}
    for f in _STAMP_COUNT_FIELDS:
        params[f] = int(stamp.get(f) or 0)
    client.run([{
        "statement": "MERGE (g:OntoSmithGraph) SET "
                     + ", ".join(f"g.{f} = ${f}"
                                 for f in GRAPH_STAMP_FIELDS),
        "parameters": params}])


def store_inventory(client) -> dict:
    """What the store physically holds, stamp or no stamp:
    {rel_edges, entities, chunks, documents}."""
    return {
        "rel_edges": client.one(
            "MATCH ()-[r:REL]->() RETURN count(r)") or 0,
        "entities": client.one(
            "MATCH (e:Entity) RETURN count(e)") or 0,
        "chunks": client.one(
            "MATCH (c:Chunk) RETURN count(c)") or 0,
        "documents": client.one(
            "MATCH (d:Document) RETURN count(d)") or 0}


def clear_store_chunks(client, keep_batch_id: str | None = None) -> dict:
    """Delete projected chunks that are not the kept
    batch's, plus Document nodes left with no chunks.
    Returns {chunks, documents} removed."""
    keep = keep_batch_id or ""
    n_chunks = client.one(
        "MATCH (c:Chunk) WHERE coalesce(c.batch_id, '') <> $keep "
        "RETURN count(c)", {"keep": keep}) or 0
    if n_chunks:
        client.run([{
            "statement": "MATCH (c:Chunk) "
                         "WHERE coalesce(c.batch_id, '') <> $keep "
                         "DETACH DELETE c",
            "parameters": {"keep": keep}}])
    n_docs = client.one(
        "MATCH (d:Document) WHERE NOT (d)-[:HAS_CHUNK]->(:Chunk) "
        "RETURN count(d)") or 0
    if n_docs:
        client.run([{
            "statement": "MATCH (d:Document) "
                         "WHERE NOT (d)-[:HAS_CHUNK]->(:Chunk) "
                         "DELETE d",
            "parameters": {}}])
    return {"chunks": int(n_chunks), "documents": int(n_docs)}


def clear_store_graph(client, keep_batch_id: str | None = None) -> dict:
    """Remove the store's projected graph except the kept
    batch's own content: other batches' REL edges and
    chunks, then Document nodes left chunk-less and Entity
    nodes left with no REL edge and no chunk mention (they
    existed only for the removed graph — the registry,
    which owns entity identity, is untouched). Returns the
    counts removed: {rel_edges, chunks, documents,
    entities}."""
    keep = keep_batch_id or ""
    n_rels = client.one(
        "MATCH ()-[r:REL]->() "
        "WHERE coalesce(r.batch_id, '') <> $keep "
        "RETURN count(r)", {"keep": keep}) or 0
    if n_rels:
        client.run([{
            "statement": "MATCH ()-[r:REL]->() "
                         "WHERE coalesce(r.batch_id, '') <> $keep "
                         "DELETE r",
            "parameters": {"keep": keep}}])
    removed = clear_store_chunks(client, keep_batch_id)
    n_ents = client.one(
        "MATCH (e:Entity) WHERE NOT (e)-[:REL]-() "
        "AND NOT ()-[:MENTIONS]->(e) RETURN count(e)") or 0
    if n_ents:
        client.run([{
            "statement": "MATCH (e:Entity) "
                         "WHERE NOT (e)-[:REL]-() "
                         "AND NOT ()-[:MENTIONS]->(e) "
                         "DETACH DELETE e",
            "parameters": {}}])
    return {"rel_edges": int(n_rels), **removed,
            "entities": int(n_ents)}


def batch_graph_identity(batch) -> dict:
    """The graph-stamp identity a batch writes: pack
    provenance + profile lineage + batch/run, read off a
    Batch object or a batch.json dict (batch_meta's
    shape). Counts are left to the caller (they describe
    the store after the write, not the batch)."""
    def get(name):
        if isinstance(batch, dict):
            return batch.get(name)
        return getattr(batch, name, None)

    prov = get("pack_provenance") or {}
    return {
        "batch_id": get("batch_id") or "",
        "run_id": get("run_id") or "",
        "pack_id": prov.get("pack_id", ""),
        "pack_version": prov.get("pack_version", ""),
        "pack_payload_sha256": prov.get("pack_payload_sha256", ""),
        "profile_family":
            get("profile_family") or prov.get("profile_family", ""),
        "profile_version": prov.get("profile_version") or "",
        "profile_hash": get("profile_hash") or "",
        "collection": get("collection") or "",
        "source": get("source") or ""}


def describe_graph_identity(ident: dict) -> str:
    """One human line naming a graph by its stamp
    identity: the pack + version when a pack binds the
    build, else the profile lineage; always the batch,
    and the triple count when the identity carries one
    (a read stamp does; an incoming batch's does not)."""
    if ident.get("pack_id"):
        head = (f"pack '{ident['pack_id']}' "
                f"{ident.get('pack_version', '')}").rstrip()
    elif ident.get("profile_family"):
        head = f"profile '{ident['profile_family']}'"
        if ident.get("profile_hash"):
            head += f" ({ident['profile_hash']})"
    else:
        head = "the core baseline"
    line = f"{head}, batch '{ident.get('batch_id', '')}'"
    if ident.get("triples") is not None:
        line += f", {ident['triples']} triples"
    return line


def check_store_write(client, incoming: dict) -> tuple[str, dict | None]:
    """Decide what a graph write under `incoming` (a
    batch_graph_identity dict) may do to the store:

    - "fresh" — the store holds no graph; write proceeds.
    - "same" — the store's stamped graph IS this batch's
      (same batch, same ontology pin): an idempotent
      re-write; VERIFY's match is the contract.
    - "replace" — the store holds a DIFFERENT graph
      (returned as the second element): the caller names
      it, clears it, writes, and re-stamps.

    Raises StoreIdentityError when the store holds graph
    content no stamp identifies (written before stamping
    existed, or by another tool): unidentified content is
    never overwritten silently — the message says what
    was found and what the operator can do."""
    stamp = read_graph_stamp(client)
    if stamp is not None:
        same = (
            stamp["batch_id"] == incoming.get("batch_id")
            and stamp["pack_payload_sha256"]
                == incoming.get("pack_payload_sha256", "")
            and stamp["profile_hash"]
                == incoming.get("profile_hash", ""))
        return ("same" if same else "replace"), stamp
    inv = store_inventory(client)
    if inv["rel_edges"] or inv["entities"] or inv["chunks"]:
        raise StoreIdentityError(
            f"the Neo4j store holds {inv['rel_edges']} REL edges, "
            f"{inv['entities']} entities and {inv['chunks']} chunks "
            f"that no OntoSmith graph stamp identifies (written "
            f"before graph stamping, or by another tool) — a build "
            f"never overwrites a graph it cannot name. If the "
            f"content is disposable, clear the database (e.g. "
            f"MATCH (n) DETACH DELETE n in Neo4j Browser) and "
            f"re-run; otherwise point NEO4J_URL at a database "
            f"OntoSmith may own. Setup: docs/connections.md "
            f"(Neo4j — the graph store).")
    return "fresh", None


def probe(client: Neo4jClient) -> str | None:
    """One cheap round trip against the configured store.
    Returns None when the store answers and authenticates,
    else a one-line description of the problem. The password
    is never part of the description."""
    import urllib.error
    try:
        client.run([{"statement": "RETURN 1 AS ok",
                     "parameters": {}}], timeout=10)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return (f"Neo4j at {client.url} rejected the "
                    f"configured credentials (HTTP {e.code}) — "
                    f"check NEO4J_USER / NEO4J_PASSWORD")
        return f"Neo4j at {client.url} answered HTTP {e.code}"
    except Exception as e:
        return (f"Neo4j at {client.url} is unreachable "
                f"({type(e).__name__}: {e})")
    return None


class Neo4jEntityRegistry(EntityRegistry):
    """EntityRegistry backed by Neo4j nodes (Phase 1).

    find_candidates uses embedding similarity (large model) + substring
    as fallback. Embeddings are cached locally per entity_id.
    """

    def __init__(self, client: Neo4jClient | None = None,
                 embedder=None, cache_path: Path | None = None):
        self.c = client or Neo4jClient()
        # entity_id must be unique — the registry's identity invariant.
        # (Lesson 2026-10-10: naive backfill created duplicate IDs,
        #  causing MATCH to hit multiple nodes and duplicate edges.)
        try:
            self.c.run([{"statement":
                "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS "
                "FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
                "parameters": {}}])
        except Exception:
            pass  # constraint may already exist or user lacks admin
        self._embedder = embedder  # lazy: created on first use
        self._cache_path = cache_path
        self._emb_cache: dict[str, list] = {}
        if cache_path and cache_path.exists():
            try:
                import pickle
                self._emb_cache = pickle.loads(cache_path.read_bytes())
            except Exception:
                pass

    def _get_embedder(self):
        if self._embedder is None:
            from .embedder import default_embedder
            self._embedder = default_embedder()
        return self._embedder

    def _save_cache(self):
        if self._cache_path:
            try:
                import pickle
                self._cache_path.write_bytes(pickle.dumps(self._emb_cache))
            except Exception:
                pass

    @staticmethod
    def _scope_clause(corpus: str | None, cross_corpus: bool) -> str:
        """Corpus namespace filter: scoped by default; the
        cross_corpus opt-in drops the filter explicitly."""
        if corpus is None or cross_corpus:
            return ""
        return "coalesce(e.corpus, '') = $corpus "

    def find_candidates(self, name: str, label: str = "",
                        limit: int = 5, corpus: str | None = None,
                        cross_corpus: bool = False) -> list[Entity]:
        # Strategy 1: embedding similarity (large model).
        # Strategy 2: substring fallback when the embedding
        # path fails at runtime. A missing embedder *binding*
        # is not a runtime failure: it refuses by name
        # (BindingKeyError) rather than silently serving the
        # weaker strategy (no local embedder exists
        # to fall back to).
        try:
            return self._find_by_embedding(name, label, limit, corpus,
                                           cross_corpus)
        except connections.BindingKeyError:
            raise
        except Exception:
            return self._find_by_substring(name, label, limit, corpus,
                                           cross_corpus)

    def _find_by_substring(self, name: str, label: str = "",
                           limit: int = 5, corpus: str | None = None,
                           cross_corpus: bool = False) -> list[Entity]:
        scope = self._scope_clause(corpus, cross_corpus)
        stmt = (
            "MATCH (e:Entity) WHERE toLower(e.name) CONTAINS toLower($q) "
            + ("AND e.label = $label " if label else "")
            + ("AND " + scope if scope else "")
            + "RETURN e.entity_id, e.name, e.label, e.aliases "
            "LIMIT $limit"
        )
        params: dict = {"q": name, "limit": limit}
        if label:
            params["label"] = label
        if scope:
            params["corpus"] = corpus
        rows = self.c.run([{"statement": stmt, "parameters": params}])[0]
        return [Entity(entity_id=r["row"][0], name=r["row"][1],
                       label=r["row"][2] or "",
                       aliases=r["row"][3] or [])
                for r in rows]

    def _find_by_embedding(self, name: str, label: str = "",
                           limit: int = 5, corpus: str | None = None,
                           cross_corpus: bool = False) -> list[Entity]:
        import numpy as np
        # Fetch all entity names (or label-filtered)
        scope = self._scope_clause(corpus, cross_corpus)
        conds = []
        if label:
            conds.append("e.label = $label")
        if scope:
            conds.append(scope.strip())
        stmt = ("MATCH (e:Entity) "
                + ("WHERE " + " AND ".join(conds) + " " if conds else "")
                + "RETURN e.entity_id, e.name, e.label, e.aliases")
        params: dict = {}
        if label:
            params["label"] = label
        if scope:
            params["corpus"] = corpus
        rows = self.c.run([{"statement": stmt, "parameters": params}])[0]
        if not rows:
            return []
        ids = [r["row"][0] for r in rows]
        names = [r["row"][1] for r in rows]

        # Embed missing entities (cache the rest)
        embedder = self._get_embedder()
        missing_idx = [i for i, eid in enumerate(ids)
                       if eid not in self._emb_cache]
        if missing_idx:
            vecs = embedder.embed([names[i] for i in missing_idx])
            for j, i in enumerate(missing_idx):
                self._emb_cache[ids[i]] = vecs[j].tolist()
            self._save_cache()

        # Cosine similarity
        mat = np.array([self._emb_cache[eid] for eid in ids],
                       dtype=np.float32)
        q = embedder.embed([name])[0]
        sims = mat @ q
        # Top-k above threshold, plus exact substring hits
        order = np.argsort(sims)[::-1]
        out = []
        seen = set()
        for i in order[:limit * 2]:  # over-fetch, filter below
            if sims[i] < 0.35:  # similarity floor
                break
            eid = ids[i]
            if eid in seen:
                continue
            seen.add(eid)
            r = rows[i]["row"]
            out.append(Entity(entity_id=r[0], name=r[1],
                              label=r[2] or "", aliases=r[3] or []))
            if len(out) >= limit:
                break
        # Always include exact substring matches (cheap, high precision)
        for ent in self._find_by_substring(name, label, limit, corpus,
                                           cross_corpus):
            if ent.entity_id not in seen:
                out.append(ent)
                seen.add(ent.entity_id)
        return out[:limit]

    def get(self, entity_id: str) -> Entity | None:
        rows = self.c.run([{
            "statement": "MATCH (e:Entity {entity_id: $id}) "
                         "RETURN e.name, e.label, e.aliases, e.corpus",
            "parameters": {"id": entity_id}}])[0]
        if not rows:
            return None
        r = rows[0]["row"]
        return Entity(entity_id=entity_id, name=r[0], label=r[1] or "",
                      aliases=r[2] or [], corpus=r[3] or "")

    def resolve(self, name: str, corpus: str | None = None) -> Entity | None:
        """Resolve a name to an entity via normalized matching.

        Checks canonical names and aliases after normalization
        (case, underscores, whitespace). For abbreviations (LHO, HSCA),
        use find_candidates (embedding) instead — resolve() is for
        surface-form variants of the same string. Corpus-scoped when
        `corpus` is given.
        """
        from .registry import normalize_name
        target = normalize_name(name)
        scope = self._scope_clause(corpus, False)
        stmt = ("MATCH (e:Entity) "
                + ("WHERE " + scope if scope else "")
                + "RETURN e.entity_id, e.name, e.aliases")
        params = {"corpus": corpus} if scope else {}
        rows = self.c.run([{"statement": stmt, "parameters": params}])[0]
        for r in rows:
            eid, ename, aliases = r["row"][0], r["row"][1], r["row"][2] or []
            if normalize_name(ename) == target:
                return Entity(entity_id=eid, name=ename, label="",
                              aliases=aliases, corpus=corpus or "")
            for alias in aliases:
                if normalize_name(alias) == target:
                    return Entity(entity_id=eid, name=ename, label="",
                                  aliases=aliases, corpus=corpus or "")
        return None

    def create(self, entity: Entity) -> str:
        # Minted unique IDs: IDs are never derived from names —
        # the legacy slug (lowercased name truncated at 40 chars)
        # silently MERGEd distinct entities whose names collided.
        from .registry import mint_entity_id
        eid = entity.entity_id or mint_entity_id(
            lambda cand: self.get(cand) is not None)
        self.c.run([{
            "statement": (
                "MERGE (e:Entity {entity_id: $id}) "
                "ON CREATE SET e.name=$name, e.label=$label, "
                "e.aliases=$aliases, e.batch_id=$batch_id, "
                "e.run_id=$run_id, e.corpus=$corpus"
            ),
            "parameters": {"id": eid, "name": entity.name,
                           "label": entity.label,
                           "aliases": entity.aliases,
                           "batch_id": entity.batch_id,
                           "run_id": entity.run_id,
                           "corpus": entity.corpus}}])
        return eid

    def merge(self, existing_id: str, new: Entity,
              model: str = "", p_same: float | None = None) -> str:
        # Merge new into existing. The cleaner name becomes canonical.
        # model/p_same accepted for interface parity with the file
        # registry; the legacy backend keeps no decision store.
        from .registry import pick_survivor
        cur = self.get(existing_id)
        best = pick_survivor([cur.name, new.name]) if cur else new.name
        # The incoming entity's own aliases are retained alongside
        # its name (union semantics, as in the file registry):
        # a merge never drops an attested surface form.
        extra = [f for f in dict.fromkeys(new.aliases)
                 if f != best and f != new.name
                 and f not in (cur.aliases if cur else [])]
        self.c.run([{
            "statement": (
                "MATCH (e:Entity {entity_id: $id}) "
                "SET e.name = $best, "
                "e.aliases = coalesce(e.aliases, []) + $alias"
            ),
            "parameters": {"id": existing_id, "best": best,
                           "alias": [new.name, *extra]}}])
        return existing_id


class Neo4jGraphStore(GraphStore):
    """GraphStore backed by Neo4j (Phase 1 and beyond)."""

    def __init__(self, client: Neo4jClient | None = None):
        self.c = client or Neo4jClient()

    def upsert_entities(self, entities) -> int:
        """Project registry entities into the graph (UNWIND-batched).

        MERGE semantics mirror pipeline.graph_store.GraphStore
        .upsert_entities so both writers produce the same projection:
        MERGE by entity_id, SET name/aliases, label only when
        non-empty, corpus via coalesce (never clears an existing one).
        Accepts pipeline.registry.Entity objects or dicts. Returns rows
        written."""
        rows = []
        for e in entities:
            if not isinstance(e, dict):
                e = {"entity_id": e.entity_id, "name": e.name,
                     "label": e.label, "aliases": e.aliases,
                     "corpus": e.corpus}
            rows.append({
                "id": e["entity_id"], "name": e["name"],
                "label": e.get("label") or "",
                "aliases": list(e.get("aliases") or []),
                "corpus": e.get("corpus") or None})
        n = 0
        for i in range(0, len(rows), 500):
            n += self.c.one(
                "UNWIND $rows AS row "
                "MERGE (e:Entity {entity_id: row.id}) "
                "SET e.name = row.name, e.aliases = row.aliases, "
                "e.label = CASE WHEN row.label = '' THEN e.label "
                "ELSE row.label END, "
                "e.corpus = coalesce(row.corpus, e.corpus) "
                "RETURN count(e)",
                {"rows": rows[i:i + 500]}) or 0
        return int(n)

    def write_triples(self, triples: list[Triple]) -> int:
        # UNWIND batching: one statement per 500 triples instead
        # of one statement per triple. On the Query API transport each
        # statement is one HTTP POST, so this is the difference between
        # ~500 round trips and ~1 per bulk write.
        # Attestation-level MERGE key, identical to
        # GraphStore.upsert_edges: {type, batch_id, run_id, doc_id,
        # att_id}. Triple carries no extractor surfaces/quote, so its
        # att_id is over (fact, doc, model) — the same material
        # project_from_registry passes for manifest rows, so both
        # writers land on the same edge for the same attestation.
        rows = [{
            "s": t.subject_id, "o": t.object_id, "rel": t.relation,
            "batch_id": t.batch_id, "run_id": t.run_id,
            "p": t.p_true, "tier": t.tier, "tag": t.tag,
            "doc_id": t.doc_id, "model": t.model, "gate": t.gate,
            "att": attestation_id(t.subject_id, t.relation,
                                  t.object_id, t.doc_id,
                                  model=t.model or ""),
        } for t in triples]
        for i in range(0, len(rows), 500):
            self.c.run([{
                "statement": (
                    "UNWIND $rows AS row "
                    "MATCH (a:Entity {entity_id: row.s}), "
                    "(b:Entity {entity_id: row.o}) "
                    "MERGE (a)-[r:REL {type: row.rel, "
                    "batch_id: row.batch_id, run_id: row.run_id, "
                    "doc_id: row.doc_id, att_id: row.att}]->(b) "
                    "SET r.p_true=row.p, r.tier=row.tier, "
                    "r.tag=row.tag, r.model=row.model, "
                    "r.gate=row.gate"
                ),
                "parameters": {"rows": rows[i:i + 500]}}])
        return len(triples)

    def delete_run(self, batch_id: str, run_id: str) -> int:
        # Count first, then delete: the previous single statement
        # ("WITH count(r) AS c DETACH DELETE r") was invalid Cypher —
        # r leaves scope after aggregation, and DETACH applies to
        # nodes, not relationships — so rollback always failed.
        params = {"b": batch_id, "r": run_id}
        n = self.c.one(
            "MATCH ()-[r:REL {batch_id: $b, run_id: $r}]->() "
            "RETURN count(r)", params) or 0
        if n:
            self.c.run([{
                "statement": (
                    "MATCH ()-[r:REL {batch_id: $b, run_id: $r}]->() "
                    "DELETE r"),
                "parameters": params}])
        # NOTE: entities stay (registry owns them); only edges are removed.
        return int(n)

    def delete_batch(self, batch_id: str) -> int:
        """Delete EVERY REL edge carrying this batch_id, across all
        runs of the batch (the retry purge). A retry mints a
        fresh run_id, so run-scoped deletion cannot reach a prior
        attempt's edges; batch scope is the retry contract's unit.
        Strictly this batch's edges — other batches are untouched.
        Entities stay (registry owns them), same invariant as
        delete_run. Returns the number of edges deleted."""
        n = self.c.one(
            "MATCH ()-[r:REL {batch_id: $b}]->() "
            "RETURN count(r)", {"b": batch_id}) or 0
        if n:
            self.c.run([{
                "statement": (
                    "MATCH ()-[r:REL {batch_id: $b}]->() "
                    "DELETE r"),
                "parameters": {"b": batch_id}}])
        return int(n)

    def traverse(self, entity_id: str, hops: int = 2,
                 limit: int = 50) -> list[dict]:
        rows = self.c.run([{
            "statement": (
                f"MATCH p=(a:Entity {{entity_id: $id}})-[r:REL*1..{hops}]-"
                f"(b:Entity) WHERE r[-1].tag='VERIFIED' "
                f"RETURN a.entity_id, [x IN r | x.type], b.entity_id "
                f"LIMIT $limit"
            ),
            "parameters": {"id": entity_id, "limit": limit}}])[0]
        return [{"from": r["row"][0], "rels": r["row"][1],
                 "to": r["row"][2]} for r in rows]
