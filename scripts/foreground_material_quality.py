"""Byte-level evidence distinguishing opaque RGB paper from alpha chalk shapes."""
import numpy as np

def has_rgb_paper_and_ink(rgba,component_mask):
    """Opaque-image override requires actual RGB paper AND actual RGB dark ink.

    Alpha geometry alone cannot identify image paper: solid chalk buttons also
    have opaque rectangular components, with glyphs encoded as alpha holes.
    Constant chalk RGB with varying alpha therefore retains alpha semantics.
    Uses the existing dark-ink200 and opaque/light245 cutoffs; no pixel mask,
    retention, identity, or geometry tolerance changes.
    """
    rgb=rgba[...,:3][component_mask]
    return bool(len(rgb) and np.any(np.any(rgb<200,axis=1)) and np.any(np.all(rgb>=245,axis=1)))
