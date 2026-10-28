"""Parsing and chunking tests: table-row sentences, furniture, chunk shape.

These are the plumbing tests the project rules allow to run without a model.
They assert the behaviour the retrieval quality depends on, and several of them
exist because the behaviour was wrong first and the index showed it.
"""

from __future__ import annotations

from app.rag.chunk import (
    CHUNKER_VERSION,
    DROP_BELOW_WORDS,
    MAX_WORDS,
    TABLE_TARGET_WORDS,
    chunk_document,
)
from app.rag.parse import (
    ParsedDoc,
    ParsedPage,
    normalise,
    parse_html,
    repeated_lines,
    table_to_sentences,
    truncate_at_references,
)

# A benefit table in the shape the real SBCs use: a two-row header where the
# lower row names each payment column, and a grouping column filled only on the
# first row of each group.
SBC_TABLE: list[list[str | None]] = [
    ["Common Medical Event", "Services You May Need", "What You Will Pay", "", "Limitations"],
    ["", "", "In-Network Provider", "Non-Network Provider", ""],
    [
        "If you have a test",
        "Diagnostic test (x-ray, blood work)",
        "40% coinsurance",
        "Not covered",
        "--------none--------",
    ],
    [
        "",
        "Imaging (CT/PET scans, MRIs)",
        "40% coinsurance",
        "Not covered",
        "Preauthorization required.",
    ],
]


class TestTableSentences:
    """A retrieved row has to stand on its own; these assert that it does."""

    def test_each_data_row_becomes_one_sentence(self) -> None:
        sentences = table_to_sentences(SBC_TABLE, subject="Bronze 4000 HSA", page=2)
        assert len(sentences) == 2

    def test_sentence_names_the_plan(self) -> None:
        """Without the plan name a price is unattributable once retrieved."""
        sentences = table_to_sentences(SBC_TABLE, subject="Bronze 4000 HSA", page=2)
        assert all("Bronze 4000 HSA" in sentence for sentence in sentences)

    def test_sentence_names_the_service(self) -> None:
        sentences = table_to_sentences(SBC_TABLE, subject="Bronze 4000 HSA", page=2)
        assert "Imaging (CT/PET scans, MRIs)" in sentences[1]

    def test_sentence_carries_each_column_header_with_its_value(self) -> None:
        """ "40% coinsurance" is meaningless without knowing which network it is."""
        sentences = table_to_sentences(SBC_TABLE, subject="Bronze 4000 HSA", page=2)
        assert "In-Network Provider" in sentences[1]
        assert "40% coinsurance" in sentences[1]
        assert "Non-Network Provider" in sentences[1]
        assert "Not covered" in sentences[1]

    def test_grouping_column_is_carried_down(self) -> None:
        """The SBC prints the common medical event once per group, not per row."""
        sentences = table_to_sentences(SBC_TABLE, subject="Bronze 4000 HSA", page=2)
        assert "if you have a test" in sentences[1].lower()

    def test_limitations_are_kept_when_present(self) -> None:
        sentences = table_to_sentences(SBC_TABLE, subject="Bronze 4000 HSA", page=2)
        assert "Preauthorization required." in sentences[1]

    def test_dash_placeholder_is_not_treated_as_content(self) -> None:
        """The template writes an empty limitations cell as a row of dashes."""
        sentences = table_to_sentences(SBC_TABLE, subject="Bronze 4000 HSA", page=2)
        assert "none" not in sentences[0].lower()
        assert "----" not in sentences[0]

    def test_a_layout_table_produces_nothing(self) -> None:
        """Only real benefit grids become sentences; layout boxes are skipped."""
        layout = [["Call us"], ["1-800-000-0000"], ["Mon-Fri"]]
        assert table_to_sentences(layout, subject="Bronze", page=1) == []

    def test_a_row_with_no_payment_values_is_skipped(self) -> None:
        """A service name with every cell empty would be a sentence saying nothing."""
        rows: list[list[str | None]] = [
            ["Common Medical Event", "Services You May Need", "What You Will Pay"],
            ["", "", "In-Network Provider"],
            ["If you have a test", "Something", "", ""],
        ]
        assert table_to_sentences(rows, subject="Bronze", page=1) == []


class TestNormalisation:
    """One word must tokenise the same way wherever it came from."""

    def test_smart_quotes_become_ascii(self) -> None:
        assert normalise("provider’s office") == "provider's office"

    def test_dashes_become_ascii(self) -> None:
        assert normalise("40%–100%") == "40%-100%"

    def test_zero_width_characters_are_removed(self) -> None:
        assert normalise("MRI​scan") == "MRIscan"

    def test_whitespace_collapses(self) -> None:
        assert normalise("a    b\tc") == "a b c"


class TestFurniture:
    """Running headers and bibliographies must not become retrievable chunks."""

    def test_a_line_on_most_pages_is_detected_as_furniture(self) -> None:
        pages = [f"Plan Name SBC\nbody {n}\nfooter" for n in range(5)]
        found = repeated_lines(pages)
        assert "Plan Name SBC" in found
        assert "footer" in found
        assert "body 1" not in found

    def test_too_few_pages_to_judge_returns_nothing(self) -> None:
        assert repeated_lines(["one page"]) == set()

    def test_a_bibliography_is_cut_from_the_document(self) -> None:
        """A citation list matches every query and answers none of them."""
        text = (
            "Breast MRI is covered when criteria are met.\n"
            "The above policy is based on the following references:\n"
            "Smith J, et al. Journal of Radiology. 2019;12:33-40."
        )
        cut = truncate_at_references(text)
        assert "criteria are met" in cut
        assert "Journal of Radiology" not in cut

    def test_a_document_without_a_bibliography_is_untouched(self) -> None:
        text = "Imaging is covered at 40% coinsurance."
        assert truncate_at_references(text) == text


class TestHtmlParsing:
    """The HealthCare.gov pages put policy text in bare divs, not paragraphs."""

    def test_text_in_a_bare_div_is_extracted(self) -> None:
        """This is the defect that lost the 180-day appeal deadline from the index."""
        html = """
        <html><body><main>
          <div class="learnParagraph-module--paragraph--4a23">
            You must file your internal appeal within 180 days (6 months) of
            receiving notice that your claim was denied.
          </div>
        </main></body></html>
        """
        doc = parse_html(html, doc_id="d", title="t", selectors=("main",))
        assert "180 days" in doc.pages[0].text

    def test_headings_are_kept_as_markdown_so_chunking_can_split_on_them(self) -> None:
        html = (
            "<html><body><main><h2>Internal appeals</h2><p>Body text here.</p></main></body></html>"
        )
        doc = parse_html(html, doc_id="d", title="t", selectors=("main",))
        assert "## Internal appeals" in doc.pages[0].text
        assert "Internal appeals" in doc.pages[0].headings

    def test_navigation_is_dropped(self) -> None:
        html = """<html><body><main>
            <div>Back to glossary</div>
            <div>Email this page Email</div>
            <p>A deductible is the amount you owe before the plan pays.</p>
        </main></body></html>"""
        doc = parse_html(html, doc_id="d", title="t", selectors=("main",))
        assert "Back to glossary" not in doc.pages[0].text
        assert "Email this page" not in doc.pages[0].text
        assert "deductible" in doc.pages[0].text

    def test_scripts_and_navigation_elements_are_removed(self) -> None:
        html = """<html><body><main>
            <script>var x = 'coverage';</script>
            <nav>Home Plans Claims</nav>
            <p>Coinsurance is your share of the cost.</p>
        </main></body></html>"""
        doc = parse_html(html, doc_id="d", title="t", selectors=("main",))
        assert "var x" not in doc.pages[0].text
        assert "Coinsurance is your share" in doc.pages[0].text

    def test_an_empty_selector_falls_back_to_the_body(self) -> None:
        """Several published pages render into a container whose id varies."""
        html = (
            "<html><body><main></main><div id='real'>"
            + "Out-of-pocket maximum is the most you pay in a year. " * 12
            + "</div></body></html>"
        )
        doc = parse_html(html, doc_id="d", title="t", selectors=("main", "#missing"))
        assert "Out-of-pocket maximum" in doc.pages[0].text


def _doc_with(lines: list[str], table: list[str] | None = None) -> ParsedDoc:
    """A one-page parsed document, for chunking tests."""
    return ParsedDoc(
        doc_id="doc",
        title="Title",
        pages=(ParsedPage(page=1, text="\n".join(lines), table_sentences=tuple(table or ())),),
    )


def _chunks(doc: ParsedDoc):  # type: ignore[no-untyped-def]
    return chunk_document(
        doc, plan_id="PLAN1", doc_type="sbc", source_file="kb/files/x.pdf", source_url="http://x"
    )


class TestChunking:
    """Chunk shape is what decides whether a benefit row can be retrieved."""

    def test_a_table_row_is_never_split(self) -> None:
        row = "Under the Bronze plan, for Imaging you pay: In-Network Provider - 40% coinsurance."
        chunks = _chunks(_doc_with(["Heading Words Here"], table=[row] * 12))
        table_chunks = [c for c in chunks if c.kind == "table"]
        assert table_chunks
        for chunk in table_chunks:
            # Every line in a table chunk is a complete row sentence.
            for line in chunk.text.splitlines():
                assert line.endswith(".")
                assert line.startswith("Under the Bronze plan")

    def test_table_chunks_stay_small_so_each_row_keeps_its_signal(self) -> None:
        """A fifteen-row chunk made its rehabilitation row unretrievable."""
        row = "Under the Bronze plan, for Service you pay: In-Network Provider - 40% coinsurance."
        chunks = _chunks(_doc_with(["Heading Words Here"], table=[row] * 20))
        table_chunks = [c for c in chunks if c.kind == "table"]
        assert len(table_chunks) > 1
        # Allow one row of overshoot, since a row is never split.
        row_words = len(row.split())
        assert all(c.word_count <= TABLE_TARGET_WORDS + row_words for c in table_chunks)

    def test_prose_chunks_respect_the_ceiling(self) -> None:
        chunks = _chunks(
            _doc_with([f"sentence number {n} with some words in it." for n in range(300)])
        )
        assert all(c.word_count <= MAX_WORDS for c in chunks if c.kind == "prose")

    def test_furniture_sized_chunks_are_dropped(self) -> None:
        chunks = _chunks(_doc_with(["Tiny"]))
        assert all(c.word_count >= DROP_BELOW_WORDS for c in chunks)

    def test_every_chunk_carries_its_citation_metadata(self) -> None:
        chunks = _chunks(_doc_with(["word " * 80]))
        assert chunks
        for chunk in chunks:
            metadata = chunk.metadata()
            for key in (
                "chunk_id",
                "doc_id",
                "plan_id",
                "doc_type",
                "title",
                "section",
                "page",
                "source_file",
                "source_url",
            ):
                assert metadata[key] != "", f"{key} is empty"

    def test_chunk_ids_are_stable_for_the_same_text(self) -> None:
        """A rebuild of unchanged documents must not churn the index."""
        doc = _doc_with(["word " * 80])
        assert [c.chunk_id for c in _chunks(doc)] == [c.chunk_id for c in _chunks(doc)]

    def test_chunk_ids_are_unique_within_a_document(self) -> None:
        doc = _doc_with(
            [f"line {n} with enough words to survive the floor here." for n in range(40)]
        )
        ids = [c.chunk_id for c in _chunks(doc)]
        assert len(ids) == len(set(ids))

    def test_plan_id_is_set_on_every_chunk(self) -> None:
        """The plan filter is only as good as the metadata it filters on."""
        chunks = _chunks(
            _doc_with(["word " * 80], table=["Under the Bronze plan, you pay: X - Y."])
        )
        assert chunks
        assert all(c.plan_id == "PLAN1" for c in chunks)

    def test_chunker_version_is_an_integer_the_manifest_can_record(self) -> None:
        assert isinstance(CHUNKER_VERSION, int)
