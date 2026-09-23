"""Deterministic checks on the rendered artifact, run before anything is published.

No model is involved: a publication gate must give the same answer every time for the same
file. Video QA sits alongside data validation and content safety as a third binding gate -
all three have to pass before an upload happens.
"""
from .readability_qa import ReadabilityReport, SceneReadability
from .readability_qa import check_plan as check_readability
from .readability_qa import check_scene as check_scene_readability
from .video_qa import (QACheck, QAStatus, VideoQAResult, check_video, probe_media,
                       sample_frame_stats, write_qa_artifact)

__all__ = ["check_video", "write_qa_artifact", "probe_media", "sample_frame_stats",
           "VideoQAResult", "QACheck", "QAStatus", "check_readability",
           "check_scene_readability", "ReadabilityReport", "SceneReadability"]
