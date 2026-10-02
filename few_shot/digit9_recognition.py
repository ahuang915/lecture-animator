"""How a neural net recognizes the digit 9 — built from the lecture diagram.

Walks through: handwritten 9 image (28x28) -> flatten to 784-vector -> hidden
layers -> output neuron for "9" fires.

Render:
    eval "$(/usr/libexec/path_helper)" && ./venv/bin/manim -ql digit9_recognition.py Digit9Recognition
"""

from manim import *
import numpy as np


def make_nine(size=28, seed=42):
    """Procedurally generate a 28x28 intensity grid shaped like the digit 9."""
    g = np.zeros((size, size))
    rng = np.random.default_rng(seed)

    # Top closed loop (annulus)
    cy, cx = 9, 14
    r_in, r_out = 3.0, 5.2
    for i in range(size):
        for j in range(size):
            d = np.sqrt((i - cy) ** 2 + (j - cx) ** 2)
            if r_in < d < r_out:
                g[i, j] = rng.uniform(0.75, 1.0)

    # Tail descending from the lower-right of the loop, slight curve
    for t in np.linspace(0, 1, 250):
        i = 12 + t * 13
        j = 19 - t * 9 + np.sin(t * np.pi) * 0.8
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di * di + dj * dj > 1:
                    continue
                ii, jj = int(round(i + di * 0.5)), int(round(j + dj * 0.5))
                if 0 <= ii < size and 0 <= jj < size:
                    g[ii, jj] = max(g[ii, jj], rng.uniform(0.7, 0.95))

    return g


class Digit9Recognition(Scene):
    def construct(self):
        np.random.seed(0)

        # --- Title ---
        title = Text("Recognizing the digit 9", font_size=44, weight=BOLD)
        sub = Text(
            "image  →  flatten  →  hidden layers  →  output",
            font_size=22, color=GREY_B,
        ).next_to(title, DOWN, buff=0.3)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.2))
        self.wait(1.2)
        self.play(FadeOut(title), FadeOut(sub))

        # --- Stage 1: the digit image ---
        nine = make_nine()
        pixel_grid = self.build_pixel_grid(nine)
        pixel_grid.move_to(ORIGIN)

        size_lab = MathTex("28 \\times 28").scale(0.7).set_color(GREY_B)
        size_lab.next_to(pixel_grid, UP, buff=0.25)
        eq_lab = MathTex("= 784 \\text{ pixels}").scale(0.6).set_color(GREY_B)
        eq_lab.next_to(size_lab, RIGHT, buff=0.25)

        self.play(FadeIn(pixel_grid, scale=0.9), run_time=1.5)
        self.play(FadeIn(size_lab), FadeIn(eq_lab))
        self.wait(0.8)
        self.play(FadeOut(size_lab), FadeOut(eq_lab))

        # --- Stage 2: linear-weights intuition (slide 23) ---
        self.show_weights_intuition(pixel_grid, nine)

        # --- Stage 3: flatten to input vector ---
        self.play(
            pixel_grid.animate.scale(0.7).to_edge(LEFT, buff=0.6),
        )

        # Pull a representative sample of pixel values from the digit for the input column
        sample_values = self.sample_intensities(nine, n=10)
        input_layer = self.build_input_layer(sample_values)
        input_layer.next_to(pixel_grid, RIGHT, buff=1.2)

        flatten_arrow = Arrow(
            pixel_grid.get_right() + RIGHT * 0.05,
            input_layer.get_left() + LEFT * 0.05,
            color=GREY_B, buff=0.1, stroke_width=3,
            max_tip_length_to_length_ratio=0.18,
        )
        flatten_label = Text("flatten", font_size=18, color=GREY_B).next_to(flatten_arrow, UP, buff=0.1)

        self.play(Create(flatten_arrow), FadeIn(flatten_label, shift=UP * 0.1))
        self.play(LaggedStart(*[FadeIn(n, scale=0.5) for n in input_layer], lag_ratio=0.06))

        bracket = Brace(input_layer, LEFT, buff=0.05).scale(0.7)
        bracket_label = Text("784", font_size=18, color=GREY_B).next_to(bracket, LEFT, buff=0.08)
        self.play(FadeIn(bracket), FadeIn(bracket_label))
        self.wait(0.6)

        # Value annotations next to a few input nodes (echoes slide 19)
        value_texts = VGroup()
        for v, node in zip(sample_values, input_layer):
            t = Text(f"{v:.2f}", font_size=14, color=GREY_B)
            t.next_to(node, LEFT, buff=0.08)
            value_texts.add(t)
        # only show top/bottom 4 to keep it sparse
        show_indices = [0, 1, 2, 3, len(input_layer) - 4, len(input_layer) - 3, len(input_layer) - 2, len(input_layer) - 1]
        keep = VGroup(*[value_texts[i] for i in show_indices])
        # Move bracket out a bit so values fit
        self.play(
            bracket.animate.shift(LEFT * 0.5),
            bracket_label.animate.shift(LEFT * 0.5),
            LaggedStart(*[FadeIn(t) for t in keep], lag_ratio=0.06),
        )
        self.wait(0.6)

        # --- Stage 3: hidden + output layers ---
        hidden_layers = VGroup()
        prev_layer = input_layer
        for _ in range(4):
            layer = self.build_layer(8, color=GREY_B)
            layer.next_to(prev_layer, RIGHT, buff=0.95)
            hidden_layers.add(layer)
            prev_layer = layer

        output_layer = self.build_layer(10, color=GREY_B)
        output_layer.next_to(prev_layer, RIGHT, buff=1.0)

        output_labels = VGroup()
        for i, node in enumerate(output_layer):
            lab = Text(str(i), font_size=16, color=GREY_B).next_to(node, RIGHT, buff=0.12)
            output_labels.add(lab)

        # Build edges between consecutive layers
        all_layers = [input_layer, *hidden_layers, output_layer]
        edge_groups = []
        for L1, L2 in zip(all_layers[:-1], all_layers[1:]):
            edges = VGroup()
            color_left = TEAL_B if L1 is input_layer else (
                ORANGE if L2 is output_layer else GREY_C
            )
            for a in L1:
                for b in L2:
                    e = Line(
                        a.get_center(), b.get_center(),
                        stroke_width=0.7,
                        stroke_opacity=0.28,
                        color=color_left,
                    )
                    edges.add(e)
            edge_groups.append(edges)

        # Animate hidden layers + edges appearing
        self.play(
            FadeOut(flatten_arrow), FadeOut(flatten_label),
            LaggedStart(*[FadeIn(L) for L in hidden_layers], lag_ratio=0.18),
            LaggedStart(*[Create(E) for E in edge_groups[:-1]], lag_ratio=0.08),
            run_time=2.2,
        )
        self.play(
            FadeIn(output_layer), FadeIn(output_labels),
            Create(edge_groups[-1]),
            run_time=1.4,
        )

        # Fit everything on screen
        full_diagram = VGroup(
            pixel_grid, bracket, bracket_label, keep,
            input_layer, *hidden_layers, output_layer, output_labels,
            *edge_groups,
        )
        self.play(full_diagram.animate.scale(0.78).move_to(ORIGIN).shift(DOWN * 0.1))
        self.wait(0.5)

        # --- Stage 4: forward propagation pulse ---
        self.forward_pulse(all_layers, edge_groups)

        # --- Stage 5: output 9 lights up ---
        target = output_layer[9]
        target_lab = output_labels[9]
        self.play(
            target.animate.set_fill(ORANGE, opacity=1).set_stroke(ORANGE, width=3),
            target_lab.animate.set_color(YELLOW).scale(1.3),
            run_time=0.6,
        )
        # Pulse it once more for emphasis
        self.play(Indicate(target, color=ORANGE, scale_factor=1.6), run_time=0.7)

        caption = Text(
            "the '9' output neuron fires strongest  →  predicted digit",
            font_size=20, color=GREY_B,
        ).to_edge(DOWN, buff=0.45)
        self.play(FadeIn(caption, shift=UP * 0.2))
        self.wait(3.0)

    # ------------------------------------------------------------ helpers

    def build_pixel_grid(self, intensities, cell=0.07):
        """28x28 pixel image as a VGroup of small squares over a dark background."""
        n = intensities.shape[0]
        bg = Square(side_length=n * cell + 0.15, color=GREY_D, stroke_width=2)
        bg.set_fill(BLACK, opacity=1.0)
        bg.move_to(ORIGIN)

        squares = VGroup()
        for i in range(n):
            for j in range(n):
                v = float(intensities[i, j])
                if v < 0.05:
                    continue  # skip near-black pixels to reduce mobject count
                sq = Square(side_length=cell, stroke_width=0)
                sq.set_fill(WHITE, opacity=v)
                sq.move_to([(j - n / 2 + 0.5) * cell, -(i - n / 2 + 0.5) * cell, 0])
                squares.add(sq)
        return VGroup(bg, squares)

    def sample_intensities(self, intensities, n=10):
        """Pull n representative values, including some bright and some dark."""
        flat = intensities.flatten()
        bright = np.sort(flat[flat > 0.5])[::-1][:max(2, n // 3)]
        dark = flat[flat < 0.2][:n - len(bright)]
        # Interleave
        out = []
        b_iter = iter(bright.tolist())
        d_iter = iter(dark.tolist())
        for k in range(n):
            try:
                out.append(next(d_iter) if k % 3 != 1 else next(b_iter))
            except StopIteration:
                out.append(0.05)
        return out

    def build_input_layer(self, values, color=BLUE):
        nodes = VGroup()
        for v in values:
            dot = Circle(
                radius=0.13, color=color, stroke_width=2,
                fill_color=color,
                fill_opacity=0.45 + 0.55 * v,
            )
            nodes.add(dot)
        nodes.arrange(DOWN, buff=0.15)
        return nodes

    def build_layer(self, size, color=GREY_B):
        nodes = VGroup()
        for _ in range(size):
            dot = Circle(
                radius=0.13, color=color, stroke_width=2,
                fill_opacity=0.2,
            )
            nodes.add(dot)
        nodes.arrange(DOWN, buff=0.15)
        return nodes

    def forward_pulse(self, layers, edge_groups):
        """Briefly light each downstream layer + flash some edges, layer by layer."""
        rng = np.random.default_rng(1)
        for li in range(len(layers) - 1):
            L_next = layers[li + 1]
            edges = edge_groups[li]
            # Pick ~25% of edges to flash
            k = max(8, len(edges) // 4)
            flash_idx = rng.choice(len(edges), size=k, replace=False)

            edge_flashes = [
                ShowPassingFlash(
                    edges[i].copy().set_stroke(color=YELLOW, width=1.6, opacity=1.0),
                    time_width=0.6,
                    run_time=0.6,
                )
                for i in flash_idx
            ]
            node_pulses = [Indicate(n, color=YELLOW, scale_factor=1.3) for n in L_next]

            self.play(*edge_flashes, *node_pulses, run_time=0.7)

    def show_weights_intuition(self, pixel_grid, intensities, cell=0.07):
        """Slide 23: bright pixels are informative, dark ones are suppressed.

        Marks dark cells with '0', rings bright cells in red, then shows the
        weighted-sum 'shadow' of the digit produced by such weights.
        """
        header = Text(
            "A neuron emphasizes bright pixels, ignores dark ones",
            font_size=22, color=GREY_B,
        ).to_edge(UP, buff=0.5)
        self.play(FadeIn(header, shift=DOWN * 0.2))

        # Slide digit left to leave room for the shadow on the right
        self.play(pixel_grid.animate.to_edge(LEFT, buff=1.6))

        n = intensities.shape[0]
        grid_center = pixel_grid.get_center()

        def cell_pos(i, j):
            return grid_center + np.array([
                (j - n / 2 + 0.5) * cell,
                -(i - n / 2 + 0.5) * cell,
                0.0,
            ])

        # '0' markers on dark cells (corners + edges, like the slide)
        dark_cells = [(1, 5), (1, 23), (14, 1), (14, 26), (26, 5), (26, 23)]
        zeros = VGroup(*[
            Text("0", font_size=13, color=ORANGE, weight=BOLD).move_to(cell_pos(i, j))
            for (i, j) in dark_cells
        ])

        # Red rings on bright cells along the digit body
        bright_cells = [(4, 14), (7, 10), (10, 18), (15, 17), (22, 12)]
        rings = VGroup(*[
            Circle(radius=cell * 1.5, color=RED, stroke_width=2.5).move_to(cell_pos(i, j))
            for (i, j) in bright_cells
        ])

        self.play(LaggedStart(*[FadeIn(z, scale=0.5) for z in zeros], lag_ratio=0.1))
        self.play(LaggedStart(*[Create(r) for r in rings], lag_ratio=0.15))
        self.wait(0.6)

        # Weighted-sum 'shadow': Gaussian-blurred digit
        try:
            from scipy.ndimage import gaussian_filter
            shadow = gaussian_filter(intensities, sigma=1.3)
        except ImportError:
            shadow = np.zeros_like(intensities)
            k = 2
            for i in range(n):
                for j in range(n):
                    i0, i1 = max(0, i - k), min(n, i + k + 1)
                    j0, j1 = max(0, j - k), min(n, j + k + 1)
                    shadow[i, j] = intensities[i0:i1, j0:j1].mean()
        if shadow.max() > 0:
            shadow = shadow / shadow.max()

        shadow_grid = self.build_pixel_grid(shadow, cell=cell)
        shadow_grid.next_to(pixel_grid, RIGHT, buff=2.4)

        arrow = Arrow(
            pixel_grid.get_right(),
            shadow_grid.get_left(),
            buff=0.2, color=GREY_B, stroke_width=3,
            max_tip_length_to_length_ratio=0.15,
        )
        sum_lab = MathTex(r"\sum w_i \, x_i").scale(0.7).next_to(arrow, UP, buff=0.1)
        shadow_caption = Text(
            "weighted sum — 'shadow' of the digit",
            font_size=18, color=GREY_B,
        ).next_to(shadow_grid, DOWN, buff=0.25)

        self.play(GrowArrow(arrow), FadeIn(sum_lab))
        self.play(FadeIn(shadow_grid, scale=0.9))
        self.play(FadeIn(shadow_caption))
        self.wait(1.6)

        note = Text(
            "this is what convolutional layers do",
            font_size=16, color=GREY_C, slant=ITALIC,
        ).to_edge(DOWN, buff=0.4)
        self.play(FadeIn(note))
        self.wait(1.8)

        # Tear down and restore pixel_grid to center for the flatten stage
        self.play(
            FadeOut(header), FadeOut(zeros), FadeOut(rings),
            FadeOut(arrow), FadeOut(sum_lab),
            FadeOut(shadow_grid), FadeOut(shadow_caption), FadeOut(note),
            pixel_grid.animate.move_to(ORIGIN),
        )
