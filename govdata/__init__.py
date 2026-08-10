"""govdata — ingestion and analysis of US public-record disclosure data.

Sources are public government endpoints: federal contract awards (USASpending),
corporate insider filings (SEC Form 4), and congressional trade disclosures
(House Clerk PTRs). Nothing here requires credentials.

The congressional data is treated as *accountability* data rather than a
trading signal: its statutory disclosure lag makes it useless for timing, and
genuinely useful for asking which public officials traded companies receiving
federal money. See ``conflicts.py``.
"""

__version__ = "0.1.0"
