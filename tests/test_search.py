"""R16 serving surface: the file store, hybrid search,
and the CLI.

Pins, in order:
- the FileServingStore read contract over both source
  forms (the shipped example's records form; a batch
  directory with a tier-2 serving index);
- store parity: the file store vs an independent test
  double of the same contract, same fixture — identical
  entity / neighborhood / graph results;
- equivalence with the lab stack: the graph arm IS
  query_stack.graph_current_ranking's math, and the
  chunk fusion IS query_stack.hybrid_ranking's RRF
  definition (both called unmodified);
- the CLI's report card, JSON form, and named refusals;
- store selection over the existing surfaces (NEO4J_URL
  configured → the §4 store; a dead Neo4j refuses by
  name, never a silent fallback).
"""
from __future__ import annotations

import io
import json
import os
import pickle
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import numpy as np

from pipeline import query_stack, search

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _worked_example_dir() -> Path:
    """The worked example as one self-contained records
    directory: its recorded triples beside its corpus. (The
    shipped examples/ tree now pairs the demo build's
    records at its root with the worked example under
    alder-finch/; the serving contracts stay pinned against
    the worked example, so assemble its records-dir form.)"""
    import shutil
    import tempfile

    d = Path(tempfile.mkdtemp(prefix="af-example-"))
    shutil.copy(EXAMPLES / "alder-finch" / "triples.jsonl",
                d / "triples.jsonl")
    shutil.copytree(EXAMPLES / "corpus", d / "corpus")
    return d


WORKED = _worked_example_dir()


def _example_store() -> search.FileServingStore:
    return search.FileServingStore(WORKED, root=ROOT)


class TestFileStoreExample(unittest.TestCase):
    """The records form over the worked example build."""

    @classmethod
    def setUpClass(cls):
        cls.store = _example_store()

    def test_describe(self):
        d = self.store.describe()
        self.assertEqual(d["kind"], "file")
        self.assertEqual(d["triples"], 7)
        self.assertEqual(d["documents"], 4)
        self.assertEqual(d["entities"], 7)
        self.assertFalse(d["embeddings"])

    def test_entities_and_lookup(self):
        names = self.store.entities()
        self.assertEqual(names["Mara Alder"], "Mara Alder")
        ent = self.store.entity("Dune")
        self.assertEqual(ent["name"], "Dune")
        self.assertIsNone(self.store.entity("Nobody"))

    def test_find_entities_ranking(self):
        hits = self.store.find_entities("Alder")
        self.assertEqual(hits[0]["name"], "Alder & Finch")
        hits = self.store.find_entities(
            "who founded Alder & Finch")
        self.assertEqual(hits[0]["entity_id"], "Alder & Finch")
        self.assertEqual(self.store.find_entities("zzz"), [])

    def test_neighborhood_shape_and_hops(self):
        n = self.store.neighborhood(["Dune"], hops=1)
        ids = {x["entity_id"] for x in n["nodes"]}
        self.assertEqual(ids, {"Dune", "Alder & Finch"})
        self.assertEqual(len(n["edges"]), 1)
        edge = n["edges"][0]
        self.assertEqual(
            set(edge), {"from", "to", "type", "tier", "tag",
                        "p_true", "batch_id", "doc_id"})
        n2 = self.store.neighborhood(["Dune"], hops=2)
        self.assertIn("Mara Alder",
                      {x["entity_id"] for x in n2["nodes"]})
        with self.assertRaises(ValueError) as cm:
            self.store.neighborhood(["Dune"], hops=5)
        self.assertIn("hops must be 1..4", str(cm.exception))

    def test_neighborhood_tier_filter(self):
        n = self.store.neighborhood(["Dune"], hops=2,
                                     tier_filter=[2])
        self.assertEqual(n["edges"], [])

    def test_search_chunks_contract(self):
        rows = self.store.search_chunks(
            text="founded OR studio", k=10)
        self.assertTrue(rows)
        row = rows[0]
        self.assertEqual(
            set(row), {"chunk_id", "doc_id", "idx", "tier",
                       "tag", "text", "score", "text_score"})
        self.assertEqual(row["doc_id"],
                         "alder-finch-history")
        # filters pin like the §4 contract
        only = self.store.search_chunks(
            text="studio", filters={"doc_id": "nope"})
        self.assertEqual(only, [])
        self.assertEqual(self.store.search_chunks(
            text="zzzqqq"), [])

    def test_triple_records_feed_inspect(self):
        from pipeline import inspect as insp
        records = self.store.triple_records()
        rec, pos, total = insp.select(
            records, subject="Priya")
        report = insp.build_report(rec, "test", pos, total)
        self.assertEqual(report["triple"]["subject"],
                         "Priya Nair")
        self.assertEqual(report["evidence"][0]["kind"],
                         "quote")
        self.assertEqual(report["gaps"], [])


# ----------------------------------------------------------------------
# batch-form fixture
# ----------------------------------------------------------------------
DOC_A = ("Acme Corp acquired Beta LLC in 2021. The Acme "
         "Corp board approved the Beta LLC acquisition "
         "unanimously and quickly.")
DOC_B = ("Gamma Inc launched a relay satellite in 2022. "
         "The Gamma Inc satellite relay serves polar "
         "stations all year round.")


def _write_batch(root: Path) -> Path:
    """A minimal batch directory: manifest, texts, routed
    attestations, the WRITE manifest, and a tier-2 index
    whose embeddings make the vector arm testable."""
    bdir = root / "batches" / "fixbatch"
    bdir.mkdir(parents=True)
    (bdir / "batch.json").write_text(json.dumps({
        "batch_id": "fixbatch", "run_id": "r1",
        "source": "fixture", "collection": "fixture",
        "doc_ids": ["doc-a", "doc-b"], "config": {},
        "metrics": {}}))
    (bdir / "texts.jsonl").write_text(
        json.dumps({"doc_id": "doc-a", "text": DOC_A}) + "\n"
        + json.dumps({"doc_id": "doc-b", "text": DOC_B})
        + "\n")
    (bdir / "routed.jsonl").write_text(
        json.dumps({
            "subject": "Acme Corp", "relation": "acquired",
            "object": "Beta LLC", "doc_id": "doc-a",
            "quote": "Acme Corp acquired Beta LLC in 2021.",
            "p_true": 0.97, "tier": 1}) + "\n"
        + json.dumps({
            "subject": "Gamma Inc", "relation": "launched",
            "object": "relay satellite", "doc_id": "doc-b",
            "quote": "Gamma Inc launched a relay satellite "
                     "in 2022.",
            "p_true": 0.9, "tier": 1}) + "\n")
    (bdir / "written_triples.jsonl").write_text(
        json.dumps({"subject_id": "ent-1",
                    "relation": "acquired",
                    "object_id": "ent-2",
                    "doc_id": "doc-a"}) + "\n")
    chunker = query_stack.FixedWindowChunker()
    meta, chunks = [], []
    for doc_id, text in (("doc-a", DOC_A), ("doc-b", DOC_B)):
        for idx, ch in chunker.chunk(text):
            meta.append({"doc_id": doc_id, "chunk_idx": idx,
                         "tag": "VERIFIED", "tier": 1,
                         "batch_id": "fixbatch"})
            chunks.append(ch)
    index = {"embeddings": np.array(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        "meta": meta, "chunks": chunks,
        "model": "fixture"}
    with open(bdir / "tier2_index.pkl", "wb") as f:
        pickle.dump(index, f)
    return bdir


class _StubEmbedder:
    """Embeds any text as the relay-satellite axis."""

    def embed(self, texts):
        return np.array([[0.0, 1.0, 0.0]] * len(texts))


class TestFileStoreBatch(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bdir = _write_batch(self.root)
        self.store = search.FileServingStore(
            self.bdir, root=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_batch_form_loads(self):
        d = self.store.describe()
        self.assertEqual(d["batch_id"], "fixbatch")
        self.assertEqual(d["triples"], 2)
        self.assertEqual(d["documents"], 2)
        self.assertTrue(d["embeddings"])
        self.assertEqual(self.store.batch_filter(),
                         {"batch_id": "fixbatch"})

    def test_vector_arm_rows(self):
        rows = self.store.search_chunks(
            vector=[0.0, 1.0, 0.0],
            filters={"batch_id": "fixbatch"})
        self.assertEqual(rows[0]["doc_id"], "doc-b")
        self.assertIn("vector_score", rows[0])
        self.assertAlmostEqual(rows[0]["vector_score"],
                               1.0, places=6)


class TestHybridSearch(unittest.TestCase):

    def test_lexical_and_graph_arms(self):
        store = _example_store()
        result = search.hybrid_search(
            store, "Who founded Alder & Finch?")
        self.assertEqual(result["arms"],
                         ["lexical", "graph"])
        self.assertEqual(result["documents"][0]["doc_id"],
                         "alder-finch-history")
        self.assertEqual(result["entities"][0]["name"],
                         "Alder & Finch")
        top = result["triples"][0]
        self.assertEqual(top["relation"], "founded by")
        self.assertIn(top["object"],
                      ("Mara Alder", "Tomas Finch"))

    def test_empty_query_refuses(self):
        with self.assertRaises(ValueError):
            search.hybrid_search(_example_store(), "  ")

    def test_vector_arm_with_stub_embedder(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = search.FileServingStore(
                _write_batch(root), root=root)
            result = search.hybrid_search(
                store, "relay satellite launched",
                embedder=_StubEmbedder())
            self.assertIn("vector", result["arms"])
            self.assertEqual(result["documents"][0]["doc_id"],
                             "doc-b")

    def test_no_spend_without_embeddings(self):
        """A store with no chunk embeddings must never
        trigger an embedding call (the $0 sample path)."""

        class Boom:
            def embed(self, texts):
                raise AssertionError("embed() was called")

        result = search.hybrid_search(
            _example_store(), "founded studio",
            embedder=Boom())
        self.assertNotIn("vector", result["arms"])
        self.assertTrue(any("vector arm inactive" in n
                            for n in result["notes"]))


class TestLabEquivalence(unittest.TestCase):
    """The product arms ARE the lab arms, called
    unmodified — not reimplementations of them."""

    def test_graph_arm_matches_graph_current_ranking(self):
        store = _example_store()
        gd = object.__new__(query_stack.GraphData)
        gd.attestations = [{
            "s_id": t["subject_id"], "o_id": t["object_id"],
            "type": t["relation"], "doc_id": t["doc_id"],
            "surface_s": t["subject"],
            "surface_o": t["object"], "quote": t["quote"],
            "p_true": t["p_true"], "tier": t["tier"]}
            for t in store.triples()]
        gd.att_tokens = [set(query_stack.tokenize(
            f"{a['surface_s']} {a['type']} "
            f"{a['surface_o']}")) for a in gd.attestations]
        for q in ("Who founded Alder & Finch?",
                  "Where is the studio located?",
                  "What does the studio produce?"):
            lab = query_stack.graph_current_ranking(gd, q)
            mine = search.hybrid_search(
                store, q)["graph_documents"]
            self.assertEqual(mine, lab, q)

    def test_chunk_fusion_matches_hybrid_ranking(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = search.FileServingStore(
                _write_batch(root), root=root)
            q = "relay satellite launched"
            qvec = np.array([0.0, 1.0, 0.0])
            with mock.patch.object(
                    query_stack, "BATCH_ID", "fixbatch"):
                lab_docs, _ = query_stack.hybrid_ranking(
                    store, qvec, q)
            mine = search.hybrid_search(
                store, q, embedder=_StubEmbedder())
            self.assertEqual(
                [d["doc_id"] for d in mine["documents"]],
                lab_docs)


# ----------------------------------------------------------------------
# parity: file store vs an independent double of the contract
# ----------------------------------------------------------------------
class _DoubleStore:
    """The serving contract re-implemented naively over
    the shipped example's raw artifacts (its own loading,
    its own chunking, a count-based lexical scorer). The
    fixture's vocabulary makes arm orderings scorer-
    independent, so parity is a contract claim, not a
    formula claim."""

    kind = "double"

    def __init__(self, base: Path):
        self._records = [json.loads(line) for line in
                         (base / "triples.jsonl")
                         .read_text().splitlines() if line]
        self._docs = {p.stem: p.read_text() for p in
                      sorted((base / "corpus").glob("*.txt"))}
        chunker = query_stack.FixedWindowChunker()
        self._chunks = []
        for doc_id, text in self._docs.items():
            for idx, ch in chunker.chunk(text):
                self._chunks.append({
                    "chunk_id": f"examples:{doc_id}:{idx}",
                    "doc_id": doc_id, "idx": idx, "tier": 1,
                    "tag": "VERIFIED", "text": ch})

    def describe(self):
        return {"kind": "double", "source": "fixture",
                "batch_id": "examples",
                "entities": len(self.entities()),
                "triples": len(self.triples()),
                "documents": len(self._docs),
                "chunks": len(self._chunks),
                "embeddings": False}

    def batch_filter(self):
        return {}

    def entities(self):
        out = {}
        for r in self._records:
            out[r["subject"]] = r["subject"]
            out[r["object"]] = r["object"]
        return out

    def entity(self, entity_id):
        if entity_id in self.entities():
            return {"entity_id": entity_id,
                    "name": entity_id, "aliases": []}
        return None

    def find_entities(self, query, limit=10):
        import re

        def toks(s):
            return set(re.findall(r"[a-z0-9]+", s.lower()))

        qtoks = toks(query)
        hits = []
        for name in self.entities():
            ntoks = toks(name)
            if ntoks and (ntoks <= qtoks or qtoks <= ntoks):
                hits.append({"entity_id": name, "name": name,
                             "aliases": []})
        hits.sort(key=lambda e: e["name"])
        return hits[:limit]

    def triples(self):
        out = []
        for r in self._records:
            out.append({
                "subject_id": r["subject"],
                "subject": r["subject"],
                "relation": r["relation"],
                "object_id": r["object"],
                "object": r["object"],
                "doc_id": r["doc_id"],
                "quote": r.get("quote", ""),
                "p_true": r["p_true"], "tier": r["tier"],
                "tag": "VERIFIED", "batch_id": "examples",
                "model": "", "gate": ""})
        return out

    def triple_records(self):
        return [dict(r) for r in self._records]

    def neighborhood(self, entity_ids, hops=1,
                     tier_filter=None, limit=200):
        # brute force: walk the record list hop by hop
        nodes, edges = {}, {}
        frontier = set(entity_ids)
        for eid in frontier:
            nodes[eid] = {"entity_id": eid, "name": eid}
        for _ in range(hops):
            nxt = set()
            for t in self.triples():
                if tier_filter and \
                        t["tier"] not in tier_filter:
                    continue
                pair = (t["subject_id"], t["object_id"])
                if frontier & set(pair):
                    key = (pair[0], t["relation"], pair[1],
                           t["batch_id"], t["doc_id"])
                    edges[key] = {
                        "from": pair[0], "to": pair[1],
                        "type": t["relation"],
                        "tier": t["tier"], "tag": t["tag"],
                        "p_true": t["p_true"],
                        "batch_id": t["batch_id"],
                        "doc_id": t["doc_id"]}
                    for side in pair:
                        if side not in nodes:
                            nodes[side] = {
                                "entity_id": side,
                                "name": side}
                            nxt.add(side)
            frontier = nxt
        return {"nodes": list(nodes.values()),
                "edges": list(edges.values())}

    def search_chunks(self, vector=None, text=None,
                      filters=None, k=10):
        if vector is not None:
            return []
        terms = {t for t in query_stack.tokenize(text or "")
                 if len(t) > 2}
        out = []
        for c in self._chunks:
            toks = query_stack.tokenize(c["text"])
            matched = terms & set(toks)
            if matched:
                out.append({**c, "score": float(len(matched)),
                            "text_score": float(len(matched))})
        return sorted(out, key=lambda d: -d["score"])[:k]


class TestStoreParity(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.file = _example_store()
        cls.double = _DoubleStore(WORKED)

    def test_entities_parity(self):
        self.assertEqual(self.file.entities(),
                         self.double.entities())
        self.assertEqual(
            self.file.find_entities("Alder"),
            self.double.find_entities("Alder"))

    def test_neighborhood_parity(self):
        for seed, hops in (("Dune", 1), ("Dune", 2),
                           ("Mara Alder", 2)):
            a = self.file.neighborhood([seed], hops=hops)
            b = self.double.neighborhood([seed], hops=hops)
            self.assertEqual(
                {n["entity_id"] for n in a["nodes"]},
                {n["entity_id"] for n in b["nodes"]})
            def key(e):
                return (e["from"], e["type"], e["to"],
                        e["doc_id"])
            self.assertEqual(
                sorted(map(key, a["edges"])),
                sorted(map(key, b["edges"])))

    def test_search_parity_documents(self):
        ra = search.hybrid_search(
            self.file, "Dune stoneware dinnerware")
        rb = search.hybrid_search(
            self.double, "Dune stoneware dinnerware")
        self.assertEqual(ra["documents"][0]["doc_id"],
                         "alder-finch-products")
        self.assertEqual(ra["documents"][0]["doc_id"],
                         rb["documents"][0]["doc_id"])

    def test_search_parity_triples_and_graph(self):
        q = "Alder Finch produces Dune"
        ra = search.hybrid_search(self.file, q)
        rb = search.hybrid_search(self.double, q)
        self.assertEqual(
            [(t["subject"], t["relation"], t["object"])
             for t in ra["triples"]],
            [(t["subject"], t["relation"], t["object"])
             for t in rb["triples"]])
        self.assertEqual(ra["graph_documents"],
                         rb["graph_documents"])
        self.assertEqual(
            [e["entity_id"] for e in ra["entities"]],
            [e["entity_id"] for e in rb["entities"]])


class TestCliAndSelection(unittest.TestCase):

    def test_cli_card(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = search.main(["What is Mr. Darcy's income?"])
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("ten thousand per annum", out)
        self.assertIn("ch-14", out)

    def test_cli_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = search.main(["studio located", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["store"]["kind"], "file")

    def test_cli_embed_keyless_refuses_by_name(self):
        # --embed with an unresolvable embedder key
        # is a named refusal (exit 1 naming the seat and the
        # variable), never a silent local model.
        err = io.StringIO()
        with mock.patch.dict(os.environ,
                             {"ONTOSMITH_EMBEDDER_KEY_ENV":
                              "STUB_MISSING_KEY"}):
            with redirect_stderr(err):
                rc = search.main(["studio", "--embed"])
        self.assertEqual(rc, 1)
        self.assertIn("search refused", err.getvalue())
        self.assertIn("embedder", err.getvalue())
        self.assertIn("STUB_MISSING_KEY", err.getvalue())

    def test_cli_refusals(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = search.main(["", "--source",
                              str(EXAMPLES)])
        self.assertEqual(rc, 1)
        self.assertIn("search refused", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            rc = search.main(["x", "--source",
                              "/nonexistent-xyz"])
        self.assertEqual(rc, 1)
        self.assertIn("serving source not found",
                      err.getvalue())

    def test_open_store_defaults_to_example(self):
        store = search.open_store(env={})
        self.assertEqual(store.kind, "file")
        self.assertEqual(store.describe()["triples"], 737)

    def test_example_candidates_cover_install_layouts(self):
        # the pip --target layout puts data files beside
        # the packages; a candidate derived from the
        # package location must exist (an installed-layout
        # check caught its absence)
        pkg_parent = Path(search.__file__).resolve().parent.parent
        self.assertIn(pkg_parent / "share" / "ontosmith"
                      / "examples",
                      search.example_candidates())

    def test_open_store_batch_missing_refuses(self):
        with self.assertRaises(search.StoreError):
            search.open_store(batch="no-such-batch", env={})

    def test_neo4j_selected_and_refuses_by_name(self):
        class DeadGS:
            c = None

            def search_chunks(self, **kw):
                raise RuntimeError("connection refused")

        env = {"NEO4J_URL": "http://localhost:9"}
        with mock.patch.object(search, "GraphStore",
                               lambda: DeadGS()):
            store = search.open_store(env=env)
            self.assertEqual(store.kind, "neo4j")
            with self.assertRaises(search.StoreError) as cm:
                store.search_chunks(text="studio")
            self.assertIn("Neo4j store", str(cm.exception))
        # forced file selection wins over the env
        store = search.open_store(store="file", env=env)
        self.assertEqual(store.kind, "file")


if __name__ == "__main__":
    unittest.main()
