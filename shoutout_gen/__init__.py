"""Shout-out slide generator: spreadsheet -> non-overlapping, click-animated PPTX.

Modules (interface -> implementation, no cycles):
    sheet    read the spreadsheet, pick the message column, order by timestamp
    metrics  measure/wrap text in Roboto exactly as the slide will render it
    layout   pack measured boxes onto slides with zero overlap
    deck     write the PPTX (textboxes + per-box click animation)
"""
