"""Unified Daily Market Byte video: one design system for the main market sections and the
Market Radar section, rendered as a single silent MP4.

    storyboard.py   what is said (all viewer text, from the editorial plan + Radar artifacts)
    theme.py        palette, type scale, safe areas
    chrome.py       shared header / section indicator / progress / footer / background
    chartkit.py     supersampled drawing surface, value->pixel geometry
    annotations.py  markers, range bands, callouts, spotlight, magnifier
    animations.py   generic animation templates keyed by event type
    scenes.py       main sections;  radar_scenes.py  Market Radar section
    composer.py     frames -> MP4, freeze frames, freeze-frame QA

The renderer fetches nothing and calculates no market value; see storyboard.py.
"""
from .composer import Composer
from .storyboard import Storyboard, build_storyboard

__all__ = ["Composer", "Storyboard", "build_storyboard"]
