"""Single-head self-attention, animated end-to-end.

Render:
    manim -pql attention.py SelfAttention     # 480p preview
    manim -pqh attention.py SelfAttention     # 1080p final
"""

from manim import *
import numpy as np


def softmax(x, axis=-1):
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


def fmt(x, decimals=2):
    """Format a number for display in a matrix cell."""
    s = f"{x:+.{decimals}f}"
    return s.replace("+", "\\,")  # subtle spacing instead of explicit +


def matrix_from(array, decimals=2, **kwargs):
    """Build a Manim Matrix from a numpy array, with consistent formatting."""
    rows = [[fmt(v, decimals) for v in row] for row in array]
    return Matrix(rows, h_buff=1.4, v_buff=0.8, **kwargs)


class SelfAttention(Scene):
    def construct(self):
        np.random.seed(7)

        # --- Setup: precompute the whole pipeline so visuals are mathematically real ---
        tokens = ["The", "cat", "sat"]
        n = len(tokens)
        d_model = 3
        d_k = 3

        X = np.round(np.random.uniform(-0.9, 0.9, (n, d_model)), 2)
        W_Q = np.round(np.random.uniform(-0.8, 0.8, (d_model, d_k)), 2)
        W_K = np.round(np.random.uniform(-0.8, 0.8, (d_model, d_k)), 2)
        W_V = np.round(np.random.uniform(-0.8, 0.8, (d_model, d_k)), 2)

        Q = np.round(X @ W_Q, 2)
        K = np.round(X @ W_K, 2)
        V = np.round(X @ W_V, 2)

        scores = np.round(Q @ K.T, 2)
        scaled = np.round(scores / np.sqrt(d_k), 2)
        attn = np.round(softmax(scaled, axis=-1), 2)
        out = np.round(attn @ V, 2)

        # --- 0. Title ---
        self.show_title()

        # --- 1. Tokens → input matrix X ---
        X_matrix = self.show_input(tokens, X)

        # --- 2. X @ W_Q, X @ W_K, X @ W_V → Q, K, V ---
        Q_matrix, K_matrix, V_matrix = self.show_projections(
            X_matrix, X, W_Q, W_K, W_V, Q, K, V
        )

        # --- 3. Q @ K^T → scores ---
        scores_matrix = self.show_scores(Q_matrix, K_matrix, Q, K, scores)

        # --- 4. Scale and softmax ---
        attn_matrix = self.show_softmax(scores_matrix, scaled, attn, d_k)

        # --- 5. attn @ V → output ---
        self.show_output(attn_matrix, V_matrix, attn, V, out, tokens)

    # ------------------------------------------------------------------ helpers

    def show_title(self):
        title = Text("Self-Attention", font_size=56, weight=BOLD)
        sub = Text("how a token looks at every other token", font_size=24, color=GREY_B)
        sub.next_to(title, DOWN, buff=0.4)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.2))
        self.wait(1.5)
        self.play(FadeOut(title), FadeOut(sub))

    def show_input(self, tokens, X):
        header = Text("1. Tokens become an input matrix X", font_size=28).to_edge(UP)
        self.play(FadeIn(header, shift=DOWN * 0.2))

        token_labels = VGroup(
            *[Text(t, font_size=32) for t in tokens]
        ).arrange(DOWN, buff=0.7).to_edge(LEFT, buff=1.2)
        self.play(
            LaggedStart(
                *[FadeIn(t, shift=RIGHT * 0.3) for t in token_labels],
                lag_ratio=0.3,
            )
        )

        # Per-token embedding rows next to each label
        row_mats = VGroup(
            *[
                matrix_from(X[i:i + 1]).scale(0.6).next_to(
                    token_labels[i], RIGHT, buff=0.5
                )
                for i in range(len(tokens))
            ]
        )
        self.play(LaggedStart(*[Write(r) for r in row_mats], lag_ratio=0.25))
        self.wait(0.5)

        # Collapse into a single X matrix on the right
        X_label = MathTex("X", "=").scale(1.0)
        X_matrix = matrix_from(X).scale(0.7)
        X_group = VGroup(X_label, X_matrix).arrange(RIGHT, buff=0.2).to_edge(RIGHT, buff=1.2)

        self.play(
            ReplacementTransform(VGroup(*row_mats), X_matrix),
            Write(X_label),
        )
        self.wait(0.5)
        annot = Text("3 tokens × 3 dims", font_size=20, color=GREY_B).next_to(X_group, DOWN, buff=0.3)
        self.play(FadeIn(annot, shift=UP * 0.2))
        self.wait(1.2)

        self.play(
            FadeOut(header),
            FadeOut(token_labels),
            FadeOut(annot),
            X_group.animate.scale(0.85).to_edge(LEFT, buff=0.7),
        )
        return X_group

    def show_projections(self, X_group, X, W_Q, W_K, W_V, Q, K, V):
        header = Text(
            "2. Project X into Queries, Keys, Values",
            font_size=28,
        ).to_edge(UP)
        self.play(FadeIn(header, shift=DOWN * 0.2))

        # Build three rows: X · W_Q = Q,  X · W_K = K,  X · W_V = V
        colors = {"Q": BLUE_B, "K": GREEN_B, "V": YELLOW_B}
        rows = VGroup()
        out_mats = {}
        for name, W, M in [("Q", W_Q, Q), ("K", W_K, K), ("V", W_V, V)]:
            color = colors[name]
            x_copy = matrix_from(X).scale(0.45)
            W_mat = matrix_from(W).scale(0.45)
            M_mat = matrix_from(M).scale(0.45)
            x_lbl = MathTex("X").scale(0.7)
            w_lbl = MathTex(f"W_{name}", color=color).scale(0.8)
            eq = MathTex("=").scale(0.7)
            res_lbl = MathTex(name, color=color).scale(0.9)
            dot = MathTex(r"\cdot").scale(0.7)

            row = VGroup(
                x_lbl, x_copy, dot, w_lbl, W_mat, eq, res_lbl, M_mat
            ).arrange(RIGHT, buff=0.18)
            rows.add(row)
            out_mats[name] = (res_lbl, M_mat)

        rows.arrange(DOWN, buff=0.4).next_to(header, DOWN, buff=0.4).scale(0.95)

        # Fade old X_group, write the three projections
        self.play(FadeOut(X_group))
        self.play(
            LaggedStart(
                *[FadeIn(r, shift=UP * 0.2) for r in rows],
                lag_ratio=0.4,
                run_time=2.5,
            )
        )
        self.wait(1.5)

        # Keep only Q, K, V matrices at the bottom — labelled
        Q_lbl, Q_mat = out_mats["Q"]
        K_lbl, K_mat = out_mats["K"]
        V_lbl, V_mat = out_mats["V"]

        Q_grp = VGroup(MathTex("Q", color=BLUE_B).scale(0.9), matrix_from(Q).scale(0.55))
        K_grp = VGroup(MathTex("K", color=GREEN_B).scale(0.9), matrix_from(K).scale(0.55))
        V_grp = VGroup(MathTex("V", color=YELLOW_B).scale(0.9), matrix_from(V).scale(0.55))
        for g in (Q_grp, K_grp, V_grp):
            g.arrange(RIGHT, buff=0.2)
        VGroup(Q_grp, K_grp, V_grp).arrange(RIGHT, buff=1.0).to_edge(DOWN, buff=0.8)

        self.play(
            FadeOut(header),
            FadeOut(rows),
            FadeIn(Q_grp), FadeIn(K_grp), FadeIn(V_grp),
        )
        self.wait(0.8)
        return Q_grp, K_grp, V_grp

    def show_scores(self, Q_grp, K_grp, Q, K, scores):
        header = Text(
            "3. Score each query against every key:  Q · Kᵀ",
            font_size=26,
        ).to_edge(UP)
        self.play(FadeIn(header, shift=DOWN * 0.2))

        # Position Q on the left, K^T to the right of it
        Q_show = VGroup(
            MathTex("Q", color=BLUE_B).scale(0.9),
            matrix_from(Q).scale(0.6),
        ).arrange(RIGHT, buff=0.2)
        KT_show = VGroup(
            MathTex("K^{T}", color=GREEN_B).scale(0.9),
            matrix_from(K.T).scale(0.6),
        ).arrange(RIGHT, buff=0.2)
        eq = MathTex("=").scale(0.9)
        scores_mat = matrix_from(scores).scale(0.6)
        scores_lbl = MathTex(r"\text{scores}").scale(0.8)
        scores_show = VGroup(scores_lbl, scores_mat).arrange(RIGHT, buff=0.2)

        line = VGroup(Q_show, KT_show, eq, scores_show).arrange(RIGHT, buff=0.4)
        line.next_to(header, DOWN, buff=0.8)

        self.play(FadeOut(Q_grp), FadeOut(K_grp))
        self.play(FadeIn(Q_show), FadeIn(KT_show))
        self.wait(0.6)
        self.play(Write(eq), FadeIn(scores_show, shift=LEFT * 0.3))
        self.wait(0.8)

        # Highlight: row i, col j = dot(q_i, k_j) — token i attending to token j
        note = Text(
            "row i, col j  =  how much token i attends to token j",
            font_size=22,
            color=GREY_B,
        ).next_to(line, DOWN, buff=0.6)
        self.play(FadeIn(note, shift=UP * 0.2))
        self.wait(1.5)

        self.play(
            FadeOut(header), FadeOut(Q_show), FadeOut(KT_show), FadeOut(eq), FadeOut(note),
            scores_show.animate.scale(1.1).to_edge(UP, buff=1.2),
        )
        return scores_show

    def show_softmax(self, scores_show, scaled, attn, d_k):
        header = Text(
            f"4. Scale by 1/√d_k, then softmax each row",
            font_size=26,
        ).to_edge(UP)
        self.play(FadeIn(header, shift=DOWN * 0.2))
        self.play(scores_show.animate.next_to(header, DOWN, buff=0.6).scale(0.9))

        # Replace scores with scaled values
        scaled_lbl = MathTex(r"\text{scaled}").scale(0.8)
        scaled_mat = matrix_from(scaled).scale(0.55)
        scaled_show = VGroup(scaled_lbl, scaled_mat).arrange(RIGHT, buff=0.2)
        scaled_show.move_to(scores_show)

        arrow1 = MathTex(r"\div \sqrt{d_k}").scale(0.7)
        arrow1.next_to(scores_show, DOWN, buff=0.4)
        self.play(FadeIn(arrow1, shift=DOWN * 0.1))
        scaled_show.next_to(arrow1, DOWN, buff=0.4)
        self.play(FadeIn(scaled_show, shift=DOWN * 0.2))
        self.wait(0.8)

        # Softmax — show as a labelled arrow then attn matrix
        arrow2 = MathTex(r"\text{softmax row-wise}").scale(0.6)
        arrow2.next_to(scaled_show, DOWN, buff=0.4)

        attn_lbl = MathTex("A", color=ORANGE).scale(0.9)
        attn_mat = matrix_from(attn).scale(0.55)
        attn_show = VGroup(attn_lbl, attn_mat).arrange(RIGHT, buff=0.2)
        attn_show.next_to(arrow2, DOWN, buff=0.4)

        self.play(FadeIn(arrow2, shift=DOWN * 0.1))
        self.play(FadeIn(attn_show, shift=DOWN * 0.2))
        self.wait(0.8)

        # Row sums = 1 — emphasize
        sum_note = Text("each row sums to 1   →   attention weights",
                        font_size=20, color=GREY_B)
        sum_note.next_to(attn_show, DOWN, buff=0.4)
        self.play(FadeIn(sum_note, shift=UP * 0.2))
        self.wait(1.5)

        # Clean up — keep only A on screen, top-left
        cleanup = [scores_show, scaled_show, arrow1, arrow2, sum_note, header]
        self.play(
            *[FadeOut(m) for m in cleanup],
            attn_show.animate.scale(1.2).to_edge(LEFT, buff=0.7).shift(UP * 0.5),
        )
        return attn_show

    def show_output(self, attn_show, V_grp, attn, V, out, tokens):
        header = Text(
            "5. Output = A · V    (weighted sum of values)",
            font_size=26,
        ).to_edge(UP)
        self.play(FadeIn(header, shift=DOWN * 0.2))

        # Bring V back next to A
        V_show = VGroup(
            MathTex("V", color=YELLOW_B).scale(0.9),
            matrix_from(V).scale(0.6),
        ).arrange(RIGHT, buff=0.2)
        eq = MathTex("=").scale(0.9)
        out_show = VGroup(
            MathTex("\\text{out}").scale(0.8),
            matrix_from(out).scale(0.6),
        ).arrange(RIGHT, buff=0.2)

        line = VGroup(attn_show.copy(), V_show, eq, out_show).arrange(RIGHT, buff=0.4)
        line.next_to(header, DOWN, buff=0.8)

        # Animate the move
        target_A = line[0]
        self.play(
            attn_show.animate.move_to(target_A).scale_to_fit_height(target_A.height),
            FadeIn(V_show, shift=LEFT * 0.3),
        )
        self.play(Write(eq), FadeIn(out_show, shift=LEFT * 0.3))
        self.wait(1.0)

        # Final takeaway
        takeaway = VGroup(
            Text("Each output row is a blend of all value rows,", font_size=22, color=GREY_B),
            Text("weighted by query–key similarity.", font_size=22, color=GREY_B),
        ).arrange(DOWN, buff=0.15).next_to(line, DOWN, buff=0.8)
        self.play(FadeIn(takeaway, shift=UP * 0.2))
        self.wait(3.0)
