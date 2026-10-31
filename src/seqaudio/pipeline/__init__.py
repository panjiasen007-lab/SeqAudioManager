"""处理管线：网络匹配 + 多源聚合 + 批量编排。"""

from .matcher import MatchResult, Matcher, ScoreWeights, similarity
from .processor import BatchOptions, Processor

__all__ = ["MatchResult", "Matcher", "ScoreWeights", "similarity", "BatchOptions", "Processor"]
