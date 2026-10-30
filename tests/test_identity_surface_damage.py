"""Identity surface damage — regression tests from the
ontology scale-test pilot (2026-10-29, 60 docs, manual seat).

The pilot's largest single loss class was not the schema:
15 of 284 kept candidates (5.3%) carried endpoints the
identity layer itself had damaged, in both directions:

- absorption UP into a containing phrase: "Afghanistan"
  rewritten to the title phrase "Attorney General of
  Afghanistan"; the place "Los Angeles" rewritten to the
  album "Battle of Los Angeles"; "Cedar Brook" rewritten
  to the coordination "South Plainfield and the Cedar
  Brook"; "Amdír" to the prose-glued "Similarly Amdír";
  a school name absorbing a trailing "To" from the next
  sentence (the source text has no period there).
- trimming DOWN of quoted titles: the census nickname
  rule (Name "Nick" Name) fired on quote-OPENED titles —
  "Suffer the Children" censused as "the Children" with
  the full title demoted to an alias, "Days Like This"
  as "Like This", "El Regalo Más Grande" lost its "El" —
  and resolution then rewrote correct endpoints down to
  the fragments.
- a nickname-headed mention form ("Mike Padden" for
  Michael John "Mike" Padden) resolved nowhere and was
  attestation-blocked although the census itself folds
  exactly that form among its own surfaces.

The repairs are deterministic and sit in the same two
places as the damage: the census nickname rule requires
a plain name token before the quoted token (a quote that
opens the mention marks a title, not an inserted
nickname); subset absorption is vetoed when the extra
tokens are function words, prose openers, or role/title
words (absorption may complete a name with name material
only); and a nickname-headed endpoint folds to its
entity exactly as census surfaces fold.
"""
import unittest

from pipeline import proposer


def _norms(census):
    return [e["norm"] for e in census["entities"]]


TITLE_DOC = (
    "The band released \"Suffer the Children\" as its "
    "first single . \"Days Like This\" was another "
    "single . The Spanish version , \"El Regalo Más "
    "Grande\" , followed ."
)

SABET_DOC = (
    "Abdul Jabar Sabet is an Afghan politician . "
    "In May 2006 Hamid Karzai appointed him Attorney "
    "General of Afghanistan . Sabet was a long time "
    "aide to former Afghan Prime Minister Gulbuddin "
    "Hekmatyar ."
)

LA_DOC = (
    "Rage Against the Machine is an American rap metal "
    "band from Los Angeles , California . The band "
    "released The Battle of Los Angeles in 1999 ."
)

BROOK_DOC = (
    "Bound Brook flows through South Plainfield and "
    "the Cedar Brook joins it southwest of Spring "
    "Lake . The Cedar Brook is a stream ."
)

EDU_DOC = (
    "Dan Chupong is an actor . He graduate bachelor "
    "'s degree from Srinakarinwirot Institute of "
    "Physical Education To stay prepared for his "
    "film roles , he runs ."
)

ELVES_DOC = (
    "Amroth was the son of Amdír . Similarly Amdír "
    "ruled Lórien . Amdír was a king of Lórien ."
)

PADDEN_DOC = (
    "Michael John \" Mike \" Padden ( born November "
    "13 , 1946 ) is a lawyer . He is a member of the "
    "Washington State Senate ."
)


class TestQuotedTitlesAreNotNicknames(unittest.TestCase):
    def test_title_surfaces_keep_their_first_token(self):
        norms = _norms(proposer.census_entities(TITLE_DOC))
        self.assertIn("suffer the children", norms)
        self.assertIn("days like this", norms)
        self.assertIn("el regalo más grande", norms)
        self.assertNotIn("the children", norms)
        self.assertNotIn("like this", norms)
        self.assertNotIn("regalo más grande", norms)

    def test_title_endpoint_stands_as_proposed(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Suffer the Children",
              "relation": "is a single by",
              "object": "the band",
              "quote": "released \"Suffer the Children\" "
                       "as its first single"}],
            TITLE_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["subject"],
                         "Suffer the Children")
        self.assertNotIn("canonicalized", kept[0])


class TestAbsorptionVeto(unittest.TestCase):
    def test_country_not_absorbed_into_title_phrase(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Abdul Jabar Sabet",
              "relation": "country of citizenship",
              "object": "Afghanistan",
              "quote": "Abdul Jabar Sabet is an Afghan "
                       "politician"}],
            SABET_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["object"], "Afghanistan")
        self.assertNotIn("canonicalized", kept[0])

    def test_person_not_absorbed_into_titled_mention(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Abdul Jabar Sabet",
              "relation": "was an aide to",
              "object": "Gulbuddin Hekmatyar",
              "quote": "aide to former Afghan Prime "
                       "Minister Gulbuddin Hekmatyar"}],
            SABET_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["object"],
                         "Gulbuddin Hekmatyar")

    def test_place_not_absorbed_into_work_title(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Rage Against the Machine",
              "relation": "is from",
              "object": "Los Angeles",
              "quote": "band from Los Angeles , "
                       "California"}],
            LA_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["object"], "Los Angeles")

    def test_fragment_not_absorbed_into_coordination(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Cedar Brook",
              "relation": "joins",
              "object": "Bound Brook",
              "quote": "the Cedar Brook joins it "
                       "southwest of Spring Lake"}],
            BROOK_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["subject"], "Cedar Brook")

    def test_name_not_absorbed_into_trailing_prose(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Dan Chupong",
              "relation": "graduated from",
              "object": "Srinakarinwirot Institute of "
                        "Physical Education",
              "quote": "from Srinakarinwirot Institute "
                       "of Physical Education"}],
            EDU_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(
            kept[0]["object"],
            "Srinakarinwirot Institute of Physical "
            "Education")

    def test_name_not_absorbed_into_leading_prose(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Amroth",
              "relation": "is the son of",
              "object": "Amdír",
              "quote": "Amroth was the son of Amdír"}],
            ELVES_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["object"], "Amdír")


ATTACHED_PADDEN_DOC = (
    "Michael John \"Mike\" Padden (born November 13, "
    "1946) is a lawyer and politician from Spokane "
    "Valley, Washington. He is a member of the "
    "Washington State Senate."
)


class TestAttachedQuoteNickname(unittest.TestCase):
    # Raw text attaches the nickname's quotes to the
    # words they wrap; the tokenized-corpus convention
    # (standalone quote tokens, the Schneider fixture)
    # is the other shape. The bridged form must attest
    # under both, or the full name censuses nowhere.
    def test_full_name_censused_with_attached_quotes(self):
        norms = _norms(proposer.census_entities(
            ATTACHED_PADDEN_DOC))
        self.assertIn("michael john padden", norms)

    def test_nickname_plus_surname_resolves_attached(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Mike Padden",
              "relation": "is a member of",
              "object": "Washington State Senate",
              "quote": "He is a member of the Washington "
                       "State Senate"}],
            ATTACHED_PADDEN_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["subject"],
                         "Michael John Padden")


DETOK_PADDEN_DOC = (
    "Michael John \"Mike \"Padden (born November 13, "
    "1946) is a lawyer and politician from Spokane "
    "Valley, Washington. He is a member of the "
    "Washington State Senate."
)


class TestDetokQuoteShape(unittest.TestCase):
    # Tokenized-then-joined text glues the closing
    # quote to the surname ('"Mike "Padden'): the mark
    # at the surname's head closes the nickname's span,
    # so the surname is name material, not a second
    # quoted token.
    def test_surname_not_treated_as_quoted(self):
        norms = _norms(proposer.census_entities(
            DETOK_PADDEN_DOC))
        self.assertIn("michael john padden", norms)

    def test_nickname_plus_surname_resolves_detok(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Mike Padden",
              "relation": "is a member of",
              "object": "Washington State Senate",
              "quote": "He is a member of the Washington "
                       "State Senate"}],
            DETOK_PADDEN_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["subject"],
                         "Michael John Padden")


class TestNicknameHeadedEndpoint(unittest.TestCase):
    def test_nickname_plus_surname_resolves(self):
        kept, blocked = proposer.guard_triples(
            [{"subject": "Mike Padden",
              "relation": "is a member of",
              "object": "Washington State Senate",
              "quote": "He is a member of the Washington "
                       "State Senate"}],
            PADDEN_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["subject"],
                         "Michael John Padden")
        self.assertEqual(
            kept[0]["canonicalized"],
            {"subject": ["Mike Padden",
                         "Michael John Padden"]})


if __name__ == "__main__":
    unittest.main()


TITLECASE_DOC = (
    "Pharmacogenetic Analysis of Polymorphisms with "
    "Survival and Toxicity in Rectal Cancer Patients "
    "Treated with Drugex.\n\n"
    "We tested whether polymorphisms would predict "
    "survival in rectal cancer. We enrolled patients "
    "with rectal cancer, and all patients were treated "
    "with Drugex after surgery.")

GENOTYPE_DOC = (
    "We analyzed the genes GSTP1, COX2, IL10, IL8 and "
    "MMP3 in patients. The IL8-251A/A genotype (rs4073) "
    "was associated with a lower risk of toxicities. "
    "The VEGFR2 H472Q Q/Q genotype (rs1870377) was "
    "associated with a higher risk of mucositis. "
    "VEGFR2 variants were genotyped in all patients.")

SRY_DOC = (
    "Two novel mutations in the SRY gene in patients.\n\n"
    "The SRY gene acts as a transcription factor. "
    "Deletion and inactivating mutations of SRY are "
    "among the known causes of XY sex reversal. "
    "Mutations in SRY were screened in the cohort.")

JARED_DOC = (
    "Jared Scot Allen (born April 3, 1982) is a former "
    "football defensive end. He played for the "
    "Minnesota Vikings for six seasons. Allen was "
    "born in Dallas.")


class TestIndependentLifeProtection(unittest.TestCase):
    # BioRED pilot (S5) damage classes: absorption into
    # surfaces that merely contain the fragment.
    def test_titlecase_line_surface_not_absorbed(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Drugex", "subject_type": "Chemical",
              "relation": "treats", "object": "rectal cancer",
              "object_type": " Disease",
              "quote": "all patients were treated with "
                     "Drugex after surgery"}],
            TITLECASE_DOC)
        self.assertEqual(kept[0]["object"], "rectal cancer")

    def test_gene_not_absorbed_into_variant_designation(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "rs4073", "subject_type": "Variant",
              "relation": "in gene", "object": "IL8",
              "object_type": "Gene",
              "quote": "IL8-251A/A genotype (rs4073) was "
                     "associated with a lower risk"}],
            GENOTYPE_DOC)
        self.assertEqual(kept[0]["object"], "IL8")

    def test_variant_not_absorbed_into_genotype_phrase(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "H472Q", "subject_type": "Variant",
              "relation": "in gene", "object": "VEGFR2",
              "object_type": "Gene",
              "quote": "The VEGFR2 H472Q Q/Q genotype "
                     "(rs1870377) was associated with a "
                     "higher risk"}],
            GENOTYPE_DOC)
        self.assertEqual(kept[0]["subject"], "H472Q")
        self.assertEqual(kept[0]["object"], "VEGFR2")

    def test_article_prepend_is_not_a_completion(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "SRY", "subject_type": "Gene",
              "relation": "causes", "object": "sex reversal",
              "object_type": "Disease",
              "quote": "mutations of SRY are among the "
                     "known causes of XY sex reversal"}],
            SRY_DOC)
        self.assertEqual(kept[0]["subject"], "SRY")

    def test_middle_name_completion_still_absorbs(self):
        # Positive control: the completion absorption
        # exists for — the fragment occurs nowhere but
        # inside the full name.
        kept, _ = proposer.guard_triples(
            [{"subject": "Jared Allen",
              "subject_type": "Person",
              "relation": "played for",
              "object": "Minnesota Vikings",
              "object_type": "Organization",
              "quote": "He played for the Minnesota "
                     "Vikings for six seasons"}],
            JARED_DOC)
        self.assertEqual(kept[0]["subject"],
                         "Jared Scot Allen")


# --- Round 3 (2026-10-30): structural absorption vetoes --
# The genre sweep (S5-S8) and the held-out build (S4)
# showed the remaining damage was one mechanism in many
# costumes: subset absorption rewrote a mention to ANY
# longer surface containing it — a typed entity built
# around it (a university in the city, the country's
# senate, a park around the river), a designation
# ('FK Fyllingsdalen'), two mentions glued across a lost
# boundary (an ALL-CAPS contract heading fused to its
# subtitle, a verse line's first word, a country fused
# to its ambassador), or a distinct entity sharing a
# name token (the company 'Cimatti', its founder). The
# vetoes in _absorption_veto read the SHAPE of the
# completion, never a list of known-bad surfaces; two
# guard-level rules complete the set — vetoes never
# prune a contested candidate field into a false unique
# answer, and canonicalisation never merges a triple's
# two distinct endpoints into one surface.

FINNEGAN_DOC = (
    "Edward Rowan Finnegan was born in Chicago. He "
    "studied at Loyola University Chicago and later "
    "served in the United States Senate.")

ASCAP_DOC = (
    "Ramey Idriss was a songwriter. Joining ASCAP in "
    "1952, Idriss registered his songs with ASCAP. He "
    "remained a member of ASCAP for decades.")

FOIX_DOC = (
    "The Foix is a river in Catalonia. The Parc del "
    "Foix is a park around its reservoir. The Foix "
    "flows into the Mediterranean Sea.")

FYLLING_DOC = (
    "Fyllingen Fotball merged with Løv-Ham to create "
    "FK Fyllingsdalen, and the two clubs ceased to "
    "exist as separate entities. The new club is "
    "based in Fyllingsdalen.")

COOP_DOC = (
    "THIS COOPERATION AGREEMENT\n\n"
    "HPIL Energytech Inc. signed the Cooperation "
    "Agreement with Ginares Group AG in 2019.")

VERSE_DOC = (
    "Thence to the bath, a beauteous pile, descend;\n"
    "Methinks Ulysses, homeward bound, had rest.\n"
    "Ulysses is the parent of Telemachus, and "
    "Telemachus honoured him.")

CIMATTI_DOC = (
    "Cimatti is an Italian motorcycle company. The "
    "company was founded by Marco Cimatti in Bologna. "
    "Cimatti is based in Bologna.")

AVALON_DOC = (
    "The Toyota Avalon is a full-size car. The Toyota "
    "Avalon was named after Avalon, the legendary "
    "island of Arthurian myth.")

HAQQANI_DOC = (
    "Husain Haqqani served as ambassador. United "
    "States Husain Haqqani talks were held in "
    "Washington. Husain Haqqani returned to "
    "Washington.")

MADHYA_DOC = (
    "Following Madhya Pradesh, the committee took up "
    "the next state. The Madhya Pradesh Legislative "
    "Assembly passed the bill. Bhopal is the capital "
    "of Madhya Pradesh.")

SMITH_DOC = (
    "John Smith (born 1960) is an engineer. John Smith "
    "joined Acme in 1990. Smith led the platform team "
    "at Acme for a decade.")

NEVADA_DOC = (
    "Novo Integrated Sciences Inc. filed as a Nevada "
    "Corporation in 2019. Its shares trade in "
    "New York.")

COLBURN_DOC = (
    "Ronald Leonard conducted the Colburn School "
    "Chamber Orchestra for years.")


class TestStructuralAbsorptionVetoes(unittest.TestCase):
    def test_place_not_absorbed_into_university(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Edward Rowan Finnegan",
              "relation": "born in", "object": "Chicago",
              "quote": "Edward Rowan Finnegan was born "
                     "in Chicago"}],
            FINNEGAN_DOC)
        self.assertEqual(kept[0]["object"], "Chicago")

    def test_country_not_absorbed_into_its_senate(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Edward Rowan Finnegan",
              "relation": "served in",
              "object": "United States",
              "quote": "later served in the United "
                     "States Senate"}],
            FINNEGAN_DOC)
        self.assertEqual(kept[0]["object"], "United States")

    def test_initialism_not_absorbed_into_phrase(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Ramey Idriss",
              "relation": "member of", "object": "ASCAP",
              "quote": "Joining ASCAP in 1952, Idriss "
                     "registered his songs with ASCAP"}],
            ASCAP_DOC)
        self.assertEqual(kept[0]["object"], "ASCAP")

    def test_river_not_absorbed_into_its_park(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Foix", "relation": "flows into",
              "object": "Mediterranean Sea",
              "quote": "The Foix flows into the "
                     "Mediterranean Sea"}],
            FOIX_DOC)
        self.assertEqual(kept[0]["subject"], "Foix")

    def test_place_not_absorbed_into_club_designation(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Fyllingen Fotball",
              "relation": "located in",
              "object": "Fyllingsdalen",
              "quote": "The new club is based in "
                     "Fyllingsdalen"}],
            FYLLING_DOC)
        self.assertEqual(kept[0]["object"], "Fyllingsdalen")

    def test_caps_heading_glue_not_absorbed(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "HPIL Energytech Inc.",
              "relation": "party to",
              "object": "Cooperation Agreement",
              "quote": "HPIL Energytech Inc. signed the "
                     "Cooperation Agreement with Ginares "
                     "Group AG in 2019"}],
            COOP_DOC)
        self.assertEqual(kept[0]["object"],
                         "Cooperation Agreement")

    def test_verse_line_glue_not_absorbed(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Ulysses",
              "relation": "is the parent of",
              "object": "Telemachus",
              "quote": "Ulysses is the parent of "
                     "Telemachus"}],
            VERSE_DOC)
        self.assertEqual(kept[0]["subject"], "Ulysses")

    def test_company_not_absorbed_into_its_founder(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Cimatti",
              "relation": "founded by",
              "object": "Marco Cimatti",
              "quote": "The company was founded by "
                     "Marco Cimatti in Bologna"}],
            CIMATTI_DOC)
        self.assertEqual(kept[0]["subject"], "Cimatti")
        self.assertEqual(kept[0]["object"], "Marco Cimatti")

    def test_glued_second_mention_not_absorbed(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Husain Haqqani",
              "relation": "ambassador to",
              "object": "United States",
              "quote": "Husain Haqqani served as "
                     "ambassador"}],
            HAQQANI_DOC)
        self.assertEqual(kept[0]["object"], "United States")

    def test_state_not_absorbed_into_corporation(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Novo Integrated Sciences Inc.",
              "relation": "incorporated in",
              "object": "Nevada",
              "quote": "filed as a Nevada Corporation "
                     "in 2019"}],
            NEVADA_DOC)
        self.assertEqual(kept[0]["object"], "Nevada")

    def test_school_not_absorbed_into_its_orchestra(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Ronald Leonard",
              "relation": "employed by",
              "object": "Colburn School",
              "quote": "Ronald Leonard conducted the "
                     "Colburn School Chamber Orchestra "
                     "for years"}],
            COLBURN_DOC)
        self.assertEqual(kept[0]["object"], "Colburn School")

    def test_contested_field_stays_ambiguous(self):
        # Both longer surfaces are veto-shaped (a prose
        # glue and a typed assembly); the vetoes must
        # not prune the field into handing the endpoint
        # to either — it stands, recorded ambiguous.
        kept, _ = proposer.guard_triples(
            [{"subject": "Bhopal",
              "relation": "capital of",
              "object": "Madhya Pradesh",
              "quote": "Bhopal is the capital of "
                     "Madhya Pradesh"}],
            MADHYA_DOC)
        self.assertEqual(kept[0]["object"], "Madhya Pradesh")
        self.assertIn("object",
                      kept[0].get("identity_ambiguous", {}))

    def test_resolution_never_merges_endpoints(self):
        # The car is named after the island; absorbing
        # the island into the car would make the triple
        # reflexive. The merge is reverted instead.
        kept, _ = proposer.guard_triples(
            [{"subject": "Toyota Avalon",
              "relation": "named after", "object": "Avalon",
              "quote": "The Toyota Avalon was named after "
                     "Avalon"}],
            AVALON_DOC)
        self.assertEqual(kept[0]["subject"], "Toyota Avalon")
        self.assertEqual(kept[0]["object"], "Avalon")

    def test_surname_completion_with_attested_full_name(self):
        # Positive control for the evidence rule: the
        # full name is attested twice, so the surname
        # completion proceeds as before.
        kept, _ = proposer.guard_triples(
            [{"subject": "Smith", "relation": "works for",
              "object": "Acme",
              "quote": "Smith led the platform team at "
                     "Acme for a decade"}],
            SMITH_DOC)
        self.assertEqual(kept[0]["subject"], "John Smith")


# --- Round 4 (2026-10-30): the evidence rule generalised --
# Round 3's generic-element veto was a hand-written list
# of English type nouns (university, senate, corporation,
# ...) — a per-language word list, and word lists are not
# a mechanism. The bake-off that replaced it measured
# what the list uniquely covered and found the coverage
# was an instance of one rule: a subset rewrite needs
# document-supplied identity evidence (see
# _rewrite_evidence), whatever the fragment's length.
# The list is gone. Two specified behaviours fall out of
# the general rule and are pinned here: a longer name the
# document attests only once does not complete a mention
# that has its own textual life (the mention stands, the
# fact is kept), and leading/trailing glue around a name
# (a show's title around a country) is refused on the
# same evidence grounds, not on the words involved.

VOICE_DOC = (
    "Tudor Chirila is a Romanian singer. He was a "
    "coach on Voice Romania for two seasons. Chirila "
    "is a citizen of Romania.")

CAREY_DOC = (
    "The Johns Hopkins Carey Business School is in "
    "Baltimore. Carey Business School admits students "
    "each fall. The Carey Business School announced a "
    "new dean this year.")


class TestEvidenceRuleGeneralised(unittest.TestCase):
    def test_country_not_absorbed_into_show_title(self):
        kept, _ = proposer.guard_triples(
            [{"subject": "Tudor Chirila",
              "relation": "country of citizenship",
              "object": "Romania",
              "quote": "Chirila is a citizen of Romania"}],
            VOICE_DOC)
        self.assertEqual(kept[0]["object"], "Romania")

    def test_hapax_full_name_does_not_complete(self):
        # The full name occurs once; the short form has
        # its own life in the document. The mention
        # stands as proposed and the fact is kept — the
        # completion is forgone, not the triple.
        kept, blocked = proposer.guard_triples(
            [{"subject": "Carey Business School",
              "relation": "located in",
              "object": "Baltimore",
              "quote": "The Johns Hopkins Carey Business "
                     "School is in Baltimore"}],
            CAREY_DOC)
        self.assertEqual(blocked, [])
        self.assertEqual(kept[0]["subject"],
                         "Carey Business School")
