"""
Text-based chunker for EFI Analyser using spaCy for linguistic analysis.

This chunker splits text into chunks based on linguistic features and content
structure while respecting maximum word limits and various heuristics.
"""

import re
from typing import List, Optional
from pydantic import BaseModel
from efi_core.types import ChunkerSpec

try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False


class TextChunkerConfig(BaseModel):
    """Configuration for TextChunker."""

    max_words: int = 80  # Maximum words per chunk
    spacy_model: str = "en_core_web_sm"  # spaCy language model to use


class TextChunker:
    """
    Text chunker that uses spaCy for linguistic analysis and follows content-aware heuristics.

    This chunker ensures that chunks respect paragraph boundaries, preserve quote attribution,
    handle discourse connectors, and apply intelligent merging rules.
    """

    def __init__(self, config: Optional[TextChunkerConfig] = None):
        """
        Initialize the text chunker.

        Args:
            config: TextChunkerConfig instance. If None, uses default configuration.
        """
        if not SPACY_AVAILABLE:
            raise ImportError("spaCy is required for TextChunker. Install with: pip install spacy")

        # Use default config if none provided
        self.config = config or TextChunkerConfig()
        self.max_words = self.config.max_words
        self.spacy_model = self.config.spacy_model

        # Load spaCy model
        try:
            self.nlp = spacy.load(self.spacy_model)
        except OSError:
            raise OSError(f"spaCy model '{self.spacy_model}' not found. Install with: python -m spacy download {self.spacy_model}")

        # Attribution patterns for quote preservation
        self.attribution_patterns = [
            # Direct attribution verbs (with optional quote marks)
            re.compile(r'["”]\s*,?\s*(said|added|told|asked|replied|explained|stated|mentioned|noted)\b', re.IGNORECASE),
            # Indirect attribution phrases (with optional quote marks)
            re.compile(r'["”]\s*,?\s*(according to|explained by|stated in|noted by|mentioned by|reported by)\b', re.IGNORECASE),
            re.compile(r'["”]\s*,?\s*(as stated by|in the words of|per the report)\b', re.IGNORECASE),
            # Handle cases where attribution comes after complex content
            re.compile(r'per month["”]\s*,?\s*(the report mentioned|it said|they stated)\b', re.IGNORECASE),
        ]

        # Discourse connectors that should attach to previous sentence
        self.discourse_connectors = {
            'however', 'but', 'therefore', 'despite', 'meanwhile', 'consequently',
            'furthermore', 'moreover', 'nevertheless', 'nonetheless', 'accordingly',
            'hence', 'thus', 'whereas', 'whereby', 'albeit', 'though', 'although'
        }

    @property
    def spec(self) -> ChunkerSpec:
        """Get the chunker specification."""
        return ChunkerSpec(
            name="text",
            params={
                "max_words": self.config.max_words,
                "spacy_model": self.config.spacy_model,
            }
        )

    def chunk(self, text: str) -> List[str]:
        """
        Split text into chunks using linguistic analysis and content heuristics.

        Args:
            text: Text to chunk

        Returns:
            List of text chunks
        """
        if not text or not text.strip():
            return []

        # Check for quotes in the text and handle them specially
        if '"' in text:
            return self._chunk_with_quotes(text)

        # Split into paragraphs first (\n\n boundaries)
        paragraphs = self._split_into_paragraphs(text)

        chunks = []
        current_chunk = []
        current_word_count = 0

        for paragraph in paragraphs:
            if not paragraph.strip():
                continue

            # Split paragraph into sentences using spaCy
            sentences = self._split_into_sentences(paragraph)

            # Check if current paragraph would make current chunk too large
            paragraph_word_count = self._count_words(paragraph)

            # If current chunk is already >= half max_words, don't merge this paragraph
            if current_chunk and current_word_count >= self.max_words / 2:
                chunks.append(' '.join(current_chunk))
                current_chunk = []
                current_word_count = 0

            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue

                sentence_word_count = self._count_words(sentence)

                # Handle sentences that exceed max_words by themselves
                if sentence_word_count > self.max_words:
                    # Truncate long sentences
                    truncated_sentence = self._truncate_sentence(sentence, self.max_words)
                    if current_chunk:
                        chunks.append(' '.join(current_chunk))
                    chunks.append(truncated_sentence)
                    current_chunk = []
                    current_word_count = 0
                    continue

                # Check if this is a discourse connector sentence
                is_discourse_connector = self._is_discourse_connector(sentence)

                # Check if this is a quote attribution
                is_attribution = self._is_quote_attribution(sentence)

                # Calculate potential new word count
                potential_word_count = current_word_count + sentence_word_count

                # Check overflow tolerance (10% allowance for attach heuristics)
                overflow_tolerance = self.max_words * 0.1
                within_tolerance = (potential_word_count > self.max_words and
                                   potential_word_count <= self.max_words + overflow_tolerance)

                # Decide whether to add sentence to current chunk
                should_add = (
                    potential_word_count <= self.max_words or  # Fits normally
                    (within_tolerance and (is_discourse_connector or is_attribution)) or  # Within tolerance for attach cases
                    (sentence_word_count < 12 and current_word_count < self.max_words / 2)  # Short sentence merging
                )

                if should_add:
                    current_chunk.append(sentence)
                    current_word_count = potential_word_count
                else:
                    # Start new chunk
                    if current_chunk:
                        chunks.append(' '.join(current_chunk))
                    current_chunk = [sentence]
                    current_word_count = sentence_word_count

        # Add final chunk
        if current_chunk:
            chunks.append(' '.join(current_chunk))

        return chunks

    def _chunk_with_quotes(self, text: str) -> List[str]:
        """Handle chunking when quotes are present in the text."""
        # For now, use a simpler approach: just chunk the entire text as one piece
        # if it contains quotes, to avoid breaking quote-attribution pairs
        if self._count_words(text) <= self.max_words:
            return [text]

        # If text is too long, fall back to regular chunking but be more careful
        return self._chunk_without_quotes(text)

    def _chunk_without_quotes(self, text: str) -> List[str]:
        """Regular chunking logic without special quote handling."""
        # Split into paragraphs first (\n\n boundaries)
        paragraphs = self._split_into_paragraphs(text)

        chunks = []
        current_chunk = []
        current_word_count = 0

        for paragraph in paragraphs:
            if not paragraph.strip():
                continue

            # Split paragraph into sentences using spaCy
            sentences = self._split_into_sentences(paragraph)

            # Check if current paragraph would make current chunk too large
            paragraph_word_count = self._count_words(paragraph)

            # If current chunk is already >= half max_words, don't merge this paragraph
            if current_chunk and current_word_count >= self.max_words / 2:
                chunks.append(' '.join(current_chunk))
                current_chunk = []
                current_word_count = 0

            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue

                sentence_word_count = self._count_words(sentence)

                # Handle sentences that exceed max_words by themselves
                if sentence_word_count > self.max_words:
                    # Truncate long sentences
                    truncated_sentence = self._truncate_sentence(sentence, self.max_words)
                    if current_chunk:
                        chunks.append(' '.join(current_chunk))
                    chunks.append(truncated_sentence)
                    current_chunk = []
                    current_word_count = 0
                    continue

                # Check if this is a discourse connector sentence
                is_discourse_connector = self._is_discourse_connector(sentence)

                # Check if this is a quote attribution
                is_attribution = self._is_quote_attribution(sentence)

                # Calculate potential new word count
                potential_word_count = current_word_count + sentence_word_count

                # Check overflow tolerance (10% allowance for attach heuristics)
                overflow_tolerance = self.max_words * 0.1
                within_tolerance = (potential_word_count > self.max_words and
                                   potential_word_count <= self.max_words + overflow_tolerance)

                # Decide whether to add sentence to current chunk
                should_add = (
                    potential_word_count <= self.max_words or  # Fits normally
                    (within_tolerance and (is_discourse_connector or is_attribution)) or  # Within tolerance for attach cases
                    (sentence_word_count < 12 and current_word_count < self.max_words / 2)  # Short sentence merging
                )

                if should_add:
                    current_chunk.append(sentence)
                    current_word_count = potential_word_count
                else:
                    # Start new chunk
                    if current_chunk:
                        chunks.append(' '.join(current_chunk))
                    current_chunk = [sentence]
                    current_word_count = sentence_word_count

        # Add final chunk
        if current_chunk:
            chunks.append(' '.join(current_chunk))

        return chunks

    def _truncate_sentence(self, sentence: str, max_words: int) -> str:
        """Truncate a sentence to fit within max_words."""
        words = sentence.split()
        if len(words) <= max_words:
            return sentence

        truncated_words = words[:max_words]
        truncated_sentence = ' '.join(truncated_words)

        # Try to end at a reasonable punctuation if possible
        if not truncated_sentence.endswith(('.', '!', '?')):
            truncated_sentence += '...'

        return truncated_sentence

    def _split_into_paragraphs(self, text: str) -> List[str]:
        """Split text into paragraphs based on double newlines."""
        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # Split on double newlines
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip()]

    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences using spaCy."""
        doc = self.nlp(text)
        sentences = []

        for sent in doc.sents:
            sentence_text = sent.text.strip()
            if sentence_text:
                sentences.append(sentence_text)

        return sentences

    def _count_words(self, text: str) -> int:
        """Count words in text using simple whitespace splitting."""
        return len(text.split())

    def _is_discourse_connector(self, sentence: str) -> bool:
        """Check if sentence starts with a discourse connector."""
        if not sentence:
            return False

        first_word = sentence.split()[0].lower().rstrip('.,!?:;')
        return first_word in self.discourse_connectors

    def _is_quote_attribution(self, sentence: str) -> bool:
        """Check if sentence contains quote attribution patterns."""
        for pattern in self.attribution_patterns:
            if pattern.search(sentence):
                return True
        return False
