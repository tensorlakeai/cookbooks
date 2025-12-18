"""
Multi-Model Text Analyzer - Demonstrates Parallel Futures in Tensorlake

This example shows how to run multiple analyses on the same input concurrently
using Tensorlake's Futures. Each analysis runs in its own container with
independent resources, and results are collected when all complete.

What this replaces if built traditionally:
- Multiple microservices for different analysis types
- An orchestration layer to fan-out requests
- asyncio/threading code to manage concurrent calls
- Load balancing across different model services
- Result aggregation and error handling
"""

from typing import List
import re
import math

from tensorlake.applications import application, function, Future


@application()
@function()
def analyze_text(text: str) -> dict:
    """
    Run multiple analyses on text in parallel.

    Each analysis runs in its own container concurrently.
    This pattern is ideal when you have independent computations
    that can all start immediately.
    """
    # Launch all analyses in parallel - each in its own container
    # .awaitable() creates the operation, .run() starts it immediately
    sentiment_future = analyze_sentiment.awaitable(text).run()
    stats_future = compute_statistics.awaitable(text).run()
    keywords_future = extract_keywords.awaitable(text).run()
    readability_future = compute_readability.awaitable(text).run()

    # Wait for all analyses to complete
    # All four are running concurrently in separate containers
    Future.wait([sentiment_future, stats_future, keywords_future, readability_future])

    # Collect results
    return {
        "sentiment": sentiment_future.result(),
        "statistics": stats_future.result(),
        "keywords": keywords_future.result(),
        "readability": readability_future.result(),
    }


@function(cpu=1, memory=1)
def analyze_sentiment(text: str) -> dict:
    """
    Analyze sentiment of text using word matching.

    Runs in its own container with 1 CPU, 1GB memory.
    In production, this could use a proper ML model with more resources.
    """
    # Word lists for simple sentiment analysis
    positive_words = {
        'good', 'great', 'excellent', 'amazing', 'wonderful', 'fantastic',
        'love', 'happy', 'joy', 'pleased', 'delighted', 'perfect', 'best',
        'beautiful', 'brilliant', 'outstanding', 'superb', 'positive',
        'success', 'successful', 'win', 'winner', 'awesome', 'nice'
    }
    negative_words = {
        'bad', 'terrible', 'awful', 'horrible', 'hate', 'sad', 'poor',
        'wrong', 'fail', 'failure', 'worst', 'ugly', 'negative', 'problem',
        'difficult', 'hard', 'unfortunately', 'disappointed', 'disappointing',
        'frustrating', 'annoying', 'angry', 'upset', 'unhappy'
    }

    words = re.findall(r'\b\w+\b', text.lower())
    word_set = set(words)

    positive_matches = word_set & positive_words
    negative_matches = word_set & negative_words

    pos_count = sum(1 for w in words if w in positive_words)
    neg_count = sum(1 for w in words if w in negative_words)

    total = pos_count + neg_count
    if total == 0:
        score = 0.5
        label = "neutral"
    else:
        score = pos_count / total
        if score > 0.6:
            label = "positive"
        elif score < 0.4:
            label = "negative"
        else:
            label = "neutral"

    return {
        "label": label,
        "score": round(score, 3),
        "positive_count": pos_count,
        "negative_count": neg_count,
        "positive_words_found": list(positive_matches)[:5],
        "negative_words_found": list(negative_matches)[:5],
    }


@function(cpu=1, memory=1)
def compute_statistics(text: str) -> dict:
    """
    Compute various text statistics.

    Independent container with its own resources.
    """
    words = re.findall(r'\b\w+\b', text)
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    characters = len(text)
    characters_no_spaces = len(text.replace(' ', '').replace('\n', ''))

    word_lengths = [len(w) for w in words]
    avg_word_length = sum(word_lengths) / len(word_lengths) if word_lengths else 0

    sentence_lengths = [len(s.split()) for s in sentences]
    avg_sentence_length = sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0

    return {
        "characters": characters,
        "characters_no_spaces": characters_no_spaces,
        "words": len(words),
        "unique_words": len(set(w.lower() for w in words)),
        "sentences": len(sentences),
        "paragraphs": len(paragraphs),
        "avg_word_length": round(avg_word_length, 2),
        "avg_sentence_length": round(avg_sentence_length, 2),
    }


@function(cpu=1, memory=1)
def extract_keywords(text: str) -> List[dict]:
    """
    Extract top keywords using TF-based ranking.

    Runs concurrently with other analyses.
    """
    stopwords = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
        'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
        'it', 'its', 'this', 'that', 'these', 'those', 'they', 'them',
        'their', 'he', 'she', 'him', 'her', 'his', 'hers', 'we', 'us', 'our',
        'you', 'your', 'i', 'me', 'my', 'what', 'which', 'who', 'whom',
        'when', 'where', 'why', 'how', 'all', 'each', 'every', 'both',
        'few', 'more', 'most', 'other', 'some', 'such', 'no', 'not', 'only',
        'same', 'so', 'than', 'too', 'very', 'just', 'also', 'now', 'here',
        'there', 'then', 'once', 'if', 'because', 'while', 'although',
        'though', 'after', 'before', 'since', 'until', 'unless', 'about'
    }

    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
    total_words = len(words)

    # Count frequencies
    word_freq = {}
    for word in words:
        if word not in stopwords:
            word_freq[word] = word_freq.get(word, 0) + 1

    # Calculate TF scores and sort
    keywords = []
    for word, count in word_freq.items():
        tf = count / total_words if total_words > 0 else 0
        keywords.append({
            "word": word,
            "count": count,
            "tf_score": round(tf, 5)
        })

    # Return top 15 keywords by frequency
    keywords.sort(key=lambda x: x["count"], reverse=True)
    return keywords[:15]


@function(cpu=1, memory=1)
def compute_readability(text: str) -> dict:
    """
    Compute readability scores (Flesch-Kincaid, etc.).

    Shows that multiple independent computations
    can run in parallel containers.
    """
    words = re.findall(r'\b\w+\b', text)
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]

    if not words or not sentences:
        return {
            "flesch_reading_ease": None,
            "flesch_kincaid_grade": None,
            "interpretation": "insufficient text"
        }

    # Count syllables (simplified)
    def count_syllables(word):
        word = word.lower()
        vowels = 'aeiouy'
        count = 0
        prev_was_vowel = False
        for char in word:
            is_vowel = char in vowels
            if is_vowel and not prev_was_vowel:
                count += 1
            prev_was_vowel = is_vowel
        if word.endswith('e') and count > 1:
            count -= 1
        return max(1, count)

    total_syllables = sum(count_syllables(w) for w in words)
    total_words = len(words)
    total_sentences = len(sentences)

    # Flesch Reading Ease
    # Higher = easier to read (90-100 = 5th grade, 0-30 = college graduate)
    fre = 206.835 - (1.015 * (total_words / total_sentences)) - (84.6 * (total_syllables / total_words))
    fre = max(0, min(100, fre))

    # Flesch-Kincaid Grade Level
    fkgl = (0.39 * (total_words / total_sentences)) + (11.8 * (total_syllables / total_words)) - 15.59
    fkgl = max(0, fkgl)

    # Interpretation
    if fre >= 90:
        interpretation = "very easy (5th grade)"
    elif fre >= 80:
        interpretation = "easy (6th grade)"
    elif fre >= 70:
        interpretation = "fairly easy (7th grade)"
    elif fre >= 60:
        interpretation = "standard (8th-9th grade)"
    elif fre >= 50:
        interpretation = "fairly difficult (10th-12th grade)"
    elif fre >= 30:
        interpretation = "difficult (college)"
    else:
        interpretation = "very difficult (college graduate)"

    return {
        "flesch_reading_ease": round(fre, 1),
        "flesch_kincaid_grade": round(fkgl, 1),
        "interpretation": interpretation,
        "total_syllables": total_syllables,
        "avg_syllables_per_word": round(total_syllables / total_words, 2),
    }


# Local testing
if __name__ == "__main__":
    from tensorlake.applications import run_local_application

    sample_text = """
    Tensorlake is a fantastic platform for building distributed applications.
    It provides excellent abstractions that make complex workflows simple.
    Developers love how easy it is to deploy and scale their Python code.
    The documentation is comprehensive and the community is very helpful.
    However, some advanced features have a learning curve that can be challenging.
    Overall, the platform delivers great value for data-intensive applications.
    """

    request = run_local_application(analyze_text, sample_text)
    import json
    print(json.dumps(request.output(), indent=2))
