"""One loading panel shared by input retrieval and model calculation."""
from time import monotonic

import streamlit as st


class LoadingFeedback:
    def __init__(self, label='Running ecosystem model'):
        self.started = monotonic()
        self.fraction = 0.
        self.panel = st.status(label, expanded=True)
        self.bar = self.panel.progress(0., text='Starting…')
        self.detail = self.panel.empty()

    def update(self, fraction, message, detail=None):
        # These fractions mark stages; remote services do not expose a percent
        # complete. Keep one bar in place when switching sources and stages.
        self.fraction = max(self.fraction, min(.99, max(0., float(fraction))))
        self.bar.progress(self.fraction,
            text=f'{message} · {monotonic()-self.started:.0f}s elapsed')
        if detail is not None:
            self.detail.caption(detail)

    def finish(self, message='Run complete', error=False):
        if not error:
            self.bar.progress(1., text=f'{message} · {monotonic()-self.started:.1f}s elapsed')
        self.panel.update(label=message, state='error' if error else 'complete', expanded=error)

    def pause(self, message):
        self.panel.update(label=message, state='complete', expanded=False)
