"""Token-to-token attention on a real sentence — intuitive view.

Render:
    eval "$(/usr/libexec/path_helper)" && ./venv/bin/manim -ql sentence_attention.py SentenceAttention
"""

from manim import *
import numpy as np


# Plausible attention weights for "The cat sat on the mat".
# Rows = query token (who is looking), columns = key token (who is being looked at).
# Numbers are made-up but pedagogically meaningful: content words attend
# to other content words; "the" mostly attends to its head noun.
SENTENCE = ["The", "cat", "sat", "on", "the", "mat"]
ATTN = np.array([
    # The  cat  sat  on  the  mat
    [0.50, 0.40, 0.05, 0.02, 0.02, 0.01],   # The  → cat (article looks at head noun)
    [0.05, 0.45, 0.35, 0.02, 0.03, 0.10],   # cat  → sat, self, mat
    [0.03, 0.55, 0.20, 0.10, 0.02, 0.10],   # sat  → cat (subject), self, mat
    [0.02, 0.05, 0.50, 0.10, 0.03, 0.30],   # on   → sat, mat (verb + object)
    [0.05, 0.05, 0.05, 0.05, 0.10, 0.70],   # the  → mat
    [0.03, 0.15, 0.30, 0.20, 0.07, 0.25],   # mat  → sat, on, self, cat
])


class SentenceAttention(Scene):
    def construct(self):
        # Title
        title = Text("Attention on a real sentence", font_size=42, weight=BOLD)
        sub = Text(
            "for each word, where does it look?",
            font_size=22, color=GREY_B,
        ).next_to(title, DOWN, buff=0.3)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.2))
        self.wait(1.2)
        self.play(FadeOut(title), FadeOut(sub))

        # The sentence — large, centered, with each word as its own Mobject
        words = VGroup(*[
            Text(w, font_size=44) for w in SENTENCE
        ]).arrange(RIGHT, buff=0.55).move_to(ORIGIN)
        # We will animate words moving up later
        self.play(LaggedStart(
            *[FadeIn(w, shift=UP * 0.2) for w in words],
            lag_ratio=0.12,
        ))
        self.wait(0.8)

        # Shift the sentence up to leave room below for arrows + heatmap
        self.play(words.animate.to_edge(UP, buff=1.2))

        # Helper bar under each word that we'll color by attention weight
        bars = VGroup(*[
            Rectangle(width=w.width * 1.1, height=0.18, stroke_width=0)
            .set_fill(BLUE_E, opacity=0.0)
            .next_to(w, DOWN, buff=0.15)
            for w in words
        ])
        self.add(bars)

        legend = Text(
            "underline = how strongly the query attends here",
            font_size=18, color=GREY_B,
        ).to_edge(DOWN, buff=0.4)
        self.play(FadeIn(legend))

        # For each query token, highlight it and draw arrows to all keys
        for q_idx in range(len(SENTENCE)):
            weights = ATTN[q_idx]
            self.show_query(words, bars, q_idx, weights)

        # Final: show full heatmap matrix on the right
        self.play(FadeOut(legend))
        self.show_heatmap(words)

    # ----------------------------------------------------------------- helpers

    def show_query(self, words, bars, q_idx, weights):
        query_word = words[q_idx]

        # Highlight the query
        original_color = query_word.get_color()
        self.play(
            query_word.animate.set_color(YELLOW).scale(1.15),
            run_time=0.4,
        )

        # Build curved arrows from query to each key, thickness ∝ weight
        arrows = VGroup()
        for k_idx, w in enumerate(weights):
            if k_idx == q_idx or w < 0.02:
                continue
            start = query_word.get_bottom() + DOWN * 0.05
            end = bars[k_idx].get_top()
            # Curve below the sentence for a clean look
            mid_dip = (start + end) / 2 + DOWN * (1.2 + 0.4 * abs(k_idx - q_idx))
            arc = CubicBezier(
                start_anchor=start,
                start_handle=start + DOWN * 1.0,
                end_handle=mid_dip,
                end_anchor=end,
            ).set_stroke(
                color=interpolate_color(GREY_D, BLUE_B, w),
                width=1 + 8 * w,
                opacity=0.3 + 0.7 * w,
            )
            arrows.add(arc)

        # Update bars to reflect weights
        bar_anims = []
        for k_idx, bar in enumerate(bars):
            w = weights[k_idx]
            bar_anims.append(
                bar.animate.set_fill(BLUE_E, opacity=min(1.0, 0.15 + 1.0 * w))
            )

        self.play(
            LaggedStart(*[Create(a) for a in arrows], lag_ratio=0.05),
            *bar_anims,
            run_time=1.0,
        )
        self.wait(0.9)

        # Clean up before next query
        self.play(
            FadeOut(arrows),
            query_word.animate.set_color(original_color).scale(1 / 1.15),
            *[b.animate.set_fill(BLUE_E, opacity=0.0) for b in bars],
            run_time=0.4,
        )

    def show_heatmap(self, words):
        header = Text("Full attention matrix", font_size=26).to_edge(UP)
        # words are already pinned to top-edge; replace them with our header view
        self.play(FadeOut(words))
        self.play(FadeIn(header, shift=DOWN * 0.2))

        n = len(SENTENCE)
        cell = 0.7
        grid = VGroup()
        labels_top = VGroup()
        labels_left = VGroup()

        for i in range(n):
            for j in range(n):
                w = ATTN[i, j]
                sq = Square(side_length=cell)
                sq.set_stroke(GREY_D, width=1)
                sq.set_fill(BLUE_B, opacity=w)
                sq.move_to([j * cell, -i * cell, 0])
                grid.add(sq)

        for j, t in enumerate(SENTENCE):
            lab = Text(t, font_size=18).rotate(PI / 6)
            lab.next_to(grid[j], UP, buff=0.15)
            labels_top.add(lab)
        for i, t in enumerate(SENTENCE):
            lab = Text(t, font_size=18)
            lab.next_to(grid[i * n], LEFT, buff=0.2)
            labels_left.add(lab)

        diagram = VGroup(grid, labels_top, labels_left).move_to(ORIGIN).shift(DOWN * 0.3)

        self.play(FadeIn(labels_top), FadeIn(labels_left))
        self.play(LaggedStart(*[FadeIn(c) for c in grid], lag_ratio=0.01, run_time=1.5))

        caption = VGroup(
            Text("row = query, column = key", font_size=20, color=GREY_B),
            Text("darker cell = more attention", font_size=20, color=GREY_B),
        ).arrange(DOWN, buff=0.1).to_edge(DOWN, buff=0.5)
        self.play(FadeIn(caption))
        self.wait(3.0)
