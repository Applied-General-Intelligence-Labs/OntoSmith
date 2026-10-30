"""The identity seat seam (identity-surgery program,
2026-10-30): guard_triples accepts an optional injected
judge for rule 4 of identity resolution. With no seat,
behavior is the deterministic rule chain (the whole
suite is the regression proof); with a seat, the seat's
per-candidate verdicts replace rule 4's filter chain
and structural vetoes — and nothing else changes:
rules 1-3, the contested-field rule, and the caller's
invariants all still apply.
"""
import unittest

from pipeline import proposer

# The probe case res00 (Re-DocRED reserve, never used
# in any tuning round): the proposer cited the singer
# by surname; the document gives her full name once,
# in the lead, and uses the surname throughout.
HAMASAKI_DOC = (
    '"Surreal "(stylized as "SURREAL ") is a song by '
    "Japanese recording artist Ayumi Hamasaki, taken "
    "from her third studio album Duty (2000). It was "
    "written by Hamasaki and produced by Max Matsuura. "
    "The song is a rock with elements of alternative "
    'rock. "Surreal "describes Hamasaki \'s madness and '
    "sense of confusion, while the themes of "
    '"Surreal "are based on Hamasaki \'s concept of '
    "loneliness, chaos, confusion, and the burden of "
    "her responsibilities, aimed mostly toward her "
    "public image as a recording artist. It was "
    "released as the fourth single from the album on "
    "27 September 2000 by Avex Trax and Avex Taiwan. "
    "Critical reception towards \"Surreal \"has been "
    "positive; the majority of critics commended the "
    "songwriting and musical delivery, and highlighted "
    "it as an album and career standout. In Japan, "
    '"Surreal "became her sixth number one on the '
    "Oricon Singles Chart, and also reached the top "
    "spot on the Japanese Count Down TV chart. "
    '"Surreal "was certified platinum by the Recording '
    "Industry Association of Japan (RIAJ) for shipments "
    "of 250,000 units. Released as a DVD Single in "
    "December 2000, it reached number three on the "
    "Oricon DVD Chart. The accompanying music video "
    "was directed by Wataru Takeishi, featuring "
    "Hamasaki walking along a beach front. With "
    "mysterious lighting hovering over several "
    "objects, she enters a jungle - like room to find "
    "a clone of herself; the clone wears the same "
    "outfit for the Duty and \"Surreal \"photo shoot. "
    '"Surreal "has been included on several concert '
    "tours and New Year Countdown shows held and "
    "hosted by Hamasaki, and has featured on majority "
    "of her greatest hits compilations.")

HAMASAKI_ITEM = {
    "subject": "Surreal", "subject_type": "CreativeWork",
    "relation": "written by", "object": "Hamasaki",
    "object_type": "Person",
    "quote": "It was written by Hamasaki and produced "
             "by Max Matsuura"}

DESMARAIS_DOC = (
    "Paul Desmarais Jr. studied at INSEAD. "
    "Paul Desmarais founded the company.")

DESMARAIS_ITEM = {
    "subject": "Paul Desmarais", "subject_type": "Person",
    "relation": "studied at", "object": "INSEAD",
    "object_type": "Organization",
    "quote": "Paul Desmarais Jr. studied at INSEAD."}

GONDAR_DOC = (
    "Debub Gondar is a Zone in the Amhara Region. This "
    "zone is named for the city of Gondar, which was "
    "the capital of Ethiopia. Semien Gondar is another "
    "zone. South Gondar is a third zone.")

GONDAR_ITEM = {
    "subject": "Debub Gondar", "subject_type": "Place",
    "relation": "named after", "object": "Gondar",
    "object_type": "Place",
    "quote": "This zone is named for the city of Gondar, "
             "which was the capital of Ethiopia"}


class TestIdentitySeat(unittest.TestCase):
    def test_rules_refuse_what_the_seat_completes(self):
        # The fresh-material class the surgery program
        # measured (probe preservation 0/4 for the rule
        # chain): a surname mention whose full name the
        # document gives in its definitional lead. The
        # veto refuses it for lack of rewrite evidence;
        # a seat that reads the envelope completes it.
        kept, _ = proposer.guard_triples(
            [dict(HAMASAKI_ITEM)], HAMASAKI_DOC)
        self.assertEqual(kept[0]["object"], "Hamasaki")
        seen = []

        def seat(ep, cand, feats):
            seen.append((ep, cand, feats))
            return cand == "ayumi hamasaki"

        kept, _ = proposer.guard_triples(
            [dict(HAMASAKI_ITEM)], HAMASAKI_DOC,
            identity_seat=seat)
        self.assertEqual(kept[0]["object"],
                         "Ayumi Hamasaki")
        self.assertTrue(seen)
        ep, cand, feats = seen[0]
        self.assertEqual((ep, cand),
                         ("hamasaki", "ayumi hamasaki"))
        self.assertEqual(feats["candidate_surface"],
                         "Ayumi Hamasaki")
        for key in ("outside", "cand_attest", "evidence",
                    "anchored", "align"):
            self.assertIn(key, feats)

    def test_seat_can_refuse_a_rules_completion(self):
        # The generational-suffix completion is a rule-4
        # decision; a seat that refuses it stands.
        kept, _ = proposer.guard_triples(
            [dict(DESMARAIS_ITEM)], DESMARAIS_DOC)
        self.assertEqual(kept[0]["subject"],
                         "Paul Desmarais Jr")
        kept, _ = proposer.guard_triples(
            [dict(DESMARAIS_ITEM)], DESMARAIS_DOC,
            identity_seat=lambda ep, cand, feats: False)
        self.assertEqual(kept[0]["subject"],
                         "Paul Desmarais")

    def test_seat_approving_a_field_is_ambiguity(self):
        # The contested-field rule survives the seat:
        # approving several candidates reports
        # ambiguity instead of picking one.
        kept, _ = proposer.guard_triples(
            [dict(GONDAR_ITEM)], GONDAR_DOC,
            identity_seat=lambda ep, cand, feats: True)
        self.assertEqual(kept[0]["object"], "Gondar")
        amb = kept[0].get("identity_ambiguous", {})
        self.assertIn("object", amb)
        self.assertGreaterEqual(len(amb["object"]), 2)


if __name__ == "__main__":
    unittest.main()
