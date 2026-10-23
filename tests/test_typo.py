from app.typo import NBSP, typograph


def test_short_words_are_glued_to_the_next_word():
    text = typograph("Однажды почтальон находит ключ в оранжерее")
    assert f"в{NBSP}оранжерее" in text


def test_consecutive_short_words():
    text = typograph("и с тех пор тревога не отпускает его")
    assert text.startswith(f"и{NBSP}с{NBSP}тех")
    assert f"не{NBSP}отпускает" in text


def test_dash_gets_non_breaking_space_before_it():
    text = typograph("в оранжерее - и с тех пор")
    assert f"оранжерее{NBSP}— и" in text


def test_short_word_inside_longer_word_is_untouched():
    assert typograph("Сова видит") == "Сова видит"


def test_capitalised_preposition():
    assert typograph("На мосту дирижёр").startswith(f"На{NBSP}мосту")
