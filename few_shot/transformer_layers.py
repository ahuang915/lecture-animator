"""Transformer architecture — block diagram of a decoder-only stack.

Render:
    eval "$(/usr/libexec/path_helper)" && ./venv/bin/manim -ql transformer_layers.py TransformerLayers
"""

from manim import *


def block(label, color, width=4.2, height=0.7, font_size=22):
    """A labelled rounded rectangle used as a sub-layer block."""
    rect = RoundedRectangle(
        corner_radius=0.12,
        width=width,
        height=height,
        stroke_color=color,
        stroke_width=2.5,
        fill_color=color,
        fill_opacity=0.15,
    )
    text = Text(label, font_size=font_size, color=WHITE)
    text.move_to(rect.get_center())
    return VGroup(rect, text)


def arrow(start, end, color=GREY_B, buff=0.05):
    return Arrow(
        start, end,
        buff=buff,
        stroke_width=3,
        max_tip_length_to_length_ratio=0.12,
        color=color,
    )


class TransformerLayers(Scene):
    def construct(self):
        # --- Title ---
        title = Text("Inside a Transformer", font_size=44, weight=BOLD)
        sub = Text(
            "decoder-only stack, the kind GPT uses",
            font_size=22, color=GREY_B,
        ).next_to(title, DOWN, buff=0.3)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.2))
        self.wait(1.0)
        self.play(FadeOut(title), FadeOut(sub))

        # --- Stage 1: Input tokens flowing in ---
        tokens = ["The", "cat", "sat", "on", "the"]
        token_grp = VGroup(*[
            Text(t, font_size=26) for t in tokens
        ]).arrange(RIGHT, buff=0.35).to_edge(LEFT, buff=0.8).shift(UP * 2.8)

        next_label = Text("(predicting next →)", font_size=18, color=GREY_B)
        next_label.next_to(token_grp, RIGHT, buff=0.4)

        self.play(LaggedStart(
            *[FadeIn(t, shift=UP * 0.2) for t in token_grp],
            lag_ratio=0.12,
        ), FadeIn(next_label))
        self.wait(0.4)

        # --- Stage 2: build the decoder block diagram on the right ---
        # Layout: vertical stack of sub-layers
        attn_block = block("Masked Self-Attention", BLUE_B, width=4.4)
        ln1 = block("Add & LayerNorm", GREY_BROWN, width=4.4, height=0.55, font_size=18)
        ffn_block = block("Feed-Forward (MLP)", GREEN_B, width=4.4)
        ln2 = block("Add & LayerNorm", GREY_BROWN, width=4.4, height=0.55, font_size=18)

        stack = VGroup(attn_block, ln1, ffn_block, ln2)
        stack.arrange(DOWN, buff=0.35)
        stack.move_to(ORIGIN).shift(RIGHT * 1.2)

        # Frame around the whole block to show "one decoder block"
        frame = SurroundingRectangle(
            stack, color=GREY_C, buff=0.35, stroke_width=2,
        )
        frame_label = Text("Decoder Block", font_size=20, color=GREY_C)
        frame_label.next_to(frame, UP, buff=0.15)

        # Internal connecting arrows
        a1 = arrow(attn_block.get_bottom(), ln1.get_top())
        a2 = arrow(ln1.get_bottom(), ffn_block.get_top())
        a3 = arrow(ffn_block.get_bottom(), ln2.get_top())

        # Residual arrows (skip connections), drawn as curves on the left side
        def residual(from_top_block, to_bottom_block, offset=2.4):
            start = from_top_block.get_left()
            end = to_bottom_block.get_left()
            via1 = start + LEFT * offset
            via2 = end + LEFT * offset
            path = VMobject(stroke_color=ORANGE, stroke_width=2.5)
            path.set_points_as_corners([start, via1, via2, end])
            # Add an arrow head at the end
            head = Arrow(
                via2, end, buff=0,
                stroke_width=2.5,
                max_tip_length_to_length_ratio=0.4,
                color=ORANGE,
            )
            return VGroup(path, head)

        # Residual goes around attn into ln1 input, and around ffn into ln2 input.
        # Simplification: show residuals as side-loops.
        res1 = residual(attn_block, ln1, offset=0.55)
        res2 = residual(ffn_block, ln2, offset=0.55)

        # Animate the block being assembled
        self.play(FadeIn(frame), FadeIn(frame_label))
        self.play(FadeIn(attn_block, shift=UP * 0.1))
        self.play(Create(a1), FadeIn(ln1, shift=UP * 0.1))
        self.play(Create(a2), FadeIn(ffn_block, shift=UP * 0.1))
        self.play(Create(a3), FadeIn(ln2, shift=UP * 0.1))
        self.play(Create(res1), Create(res2))
        self.wait(0.8)

        # --- Stage 3: data flows in from tokens, through the block ---
        embed_label = Text("Embedding + Positional", font_size=20, color=GREY_B)
        embed_label.next_to(stack, UP, buff=1.1).align_to(stack, LEFT).shift(RIGHT * 0.2)
        # arrow from tokens down to the block
        flow_arrow = arrow(
            token_grp.get_bottom() + RIGHT * 1.5,
            attn_block.get_top(),
        )

        self.play(FadeIn(embed_label, shift=DOWN * 0.2))
        self.play(Create(flow_arrow))

        # Flowing dot to suggest activation passing through
        dot = Dot(color=YELLOW, radius=0.12)
        dot.move_to(token_grp.get_bottom() + RIGHT * 1.5)
        path_points = [
            token_grp.get_bottom() + RIGHT * 1.5,
            attn_block.get_top(),
            attn_block.get_bottom(),
            ln1.get_top(),
            ln1.get_bottom(),
            ffn_block.get_top(),
            ffn_block.get_bottom(),
            ln2.get_top(),
            ln2.get_bottom(),
        ]
        self.add(dot)
        for p in path_points:
            self.play(dot.animate.move_to(p), run_time=0.25)
        self.wait(0.3)

        # --- Stage 4: zoom out — block becomes one of N layers ---
        block_group = VGroup(
            frame, frame_label, attn_block, ln1, ffn_block, ln2,
            a1, a2, a3, res1, res2,
        )

        # Shrink the detailed block down to a thumbnail
        thumb = block_group.copy()
        thumb_target = thumb.scale(0.32).move_to(ORIGIN + RIGHT * 1.0 + UP * 0.3)
        # Replace the original with a small thumbnail
        self.play(
            FadeOut(dot),
            FadeOut(flow_arrow),
            FadeOut(embed_label),
            Transform(block_group, thumb_target),
        )

        # Stack: N copies of the thumbnail, vertical
        N = 5
        stack_copies = VGroup()
        for i in range(N):
            copy = block_group.copy().shift(DOWN * 1.05 * i)
            stack_copies.add(copy)
        stack_copies.move_to(ORIGIN).shift(RIGHT * 1.0)

        # Fade the original transformed group out and replace with the stack
        # (block_group is now the shrunk version)
        self.play(
            FadeOut(block_group),
            LaggedStart(
                *[FadeIn(c, shift=DOWN * 0.2) for c in stack_copies],
                lag_ratio=0.15,
            ),
            run_time=1.8,
        )

        n_label = MathTex(r"\times\ N\ \text{layers}", color=GREY_B).scale(0.8)
        n_label.next_to(stack_copies, LEFT, buff=0.5)
        self.play(FadeIn(n_label, shift=RIGHT * 0.2))

        # --- Stage 5: output head ---
        head = block("Linear → Softmax → next-token probs", PURPLE_B, width=5.6, height=0.7, font_size=20)
        head.next_to(stack_copies, DOWN, buff=0.5)
        out_arrow = arrow(stack_copies.get_bottom(), head.get_top())

        prediction = Text("'mat'", font_size=28, color=YELLOW, weight=BOLD)
        prediction.next_to(head, DOWN, buff=0.5)
        pred_arrow = arrow(head.get_bottom(), prediction.get_top())

        self.play(Create(out_arrow), FadeIn(head, shift=UP * 0.2))
        self.play(Create(pred_arrow), FadeIn(prediction, scale=0.7))
        self.wait(2.0)

        # Final caption
        caption = Text(
            "every word's embedding flows through N identical blocks",
            font_size=20, color=GREY_B,
        ).to_edge(DOWN, buff=0.3)
        self.play(FadeIn(caption, shift=UP * 0.2))
        self.wait(2.0)
