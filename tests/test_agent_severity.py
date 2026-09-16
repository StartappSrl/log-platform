"""
Test di non-regressione per il riconoscimento di gravita' nei log Linux
generici (basato su parole chiave, dato che questi file non hanno un
campo di gravita' esplicito come i log Windows).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))


def test_error_keywords_detected():
    import agent

    assert agent._guess_level_from_text("kernel: ERROR: disk write failed") == 3
    assert agent._guess_level_from_text("CRITICAL: out of memory") == 3
    assert agent._guess_level_from_text("Failed to start nginx.service") == 3
    assert agent._guess_level_from_text("Exception in thread main") == 3


def test_warning_keywords_detected():
    import agent

    assert agent._guess_level_from_text("WARNING: disk usage at 85%") == 4
    assert agent._guess_level_from_text("This function is deprecated") == 4


def test_normal_messages_default_to_info():
    import agent

    assert agent._guess_level_from_text("Accepted password for root from 1.2.3.4") == 6
    assert agent._guess_level_from_text("Normal informational message") == 6


def test_error_wins_over_warning_when_both_present():
    import agent

    assert agent._guess_level_from_text("a line with both ERROR and warning mixed") == 3
