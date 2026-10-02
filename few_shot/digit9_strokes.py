"""Slide 22 intuition: hidden-layer neurons learn strokes of the digit 9.

Each color = one stroke. Early-layer neurons hold single strokes; deeper
layers progressively combine them, culminating in a neuron that holds the
assembled 9.

Render:
    eval "$(/usr/libexec/path_helper)" && ./venv/bin/manim -ql digit9_strokes.py Digit9Strokes
"""

from manim import *
import numpy as np


STROKE_COLORS = {
    "black":     "#ededed",  # top arc (would be black on a white slide)
    "red":       "#e63946",
    "lightblue": "#48cae4",
    "purple":    "#c77dff",
    "orange":    "#f48c06",
    "brown":     "#b08968",
}
STROKE_NAMES = ["black", "red", "lightblue", "purple", "orange", "brown"]


# What strokes each neuron at (layer_idx, neuron_idx) holds.
# Empty list -> a plain unfilled neuron (matches the slide's many empty rows).
LAYER_CONTENTS = [
    # L1 — one stroke per neuron (rows 0..5)
    [["black"], ["red"], ["lightblue"], ["purple"], ["orange"], ["brown"], []],
    # L2 — sliding-window pairs
    [["black", "red"],
     ["red", "lightblue"],
     ["lightblue", "purple"],
     ["purple", "orange"],
     ["orange", "brown"],
     [], []],
    # L3 — sliding-window triples
    [["black", "red", "lightblue"],
     ["red", "lightblue", "purple"],
     ["lightblue", "purple", "orange"],
     ["purple", "orange", "brown"],
     [], [], []],
    # L4 — deep combinations, ending in the full assembled 9
    [["black", "red", "lightblue", "purple"],
     ["red", "lightblue", "purple", "orange"],
     STROKE_NAMES,
     [], [], [], []],
]


def make_stroke_defs(stroke_width=10):
    """VMobjects for each of the six strokes that together form the 9."""
    strokes = {}

    def smooth(points, color, sw=stroke_width):
        m = VMobject(color=color, stroke_width=sw)
        m.set_points_smoothly([np.array(p) for p in points])
        return m

    # Top cap of the loop
    strokes["black"] = smooth(
        [(-0.20, 0.95, 0), (0.00, 1.00, 0), (0.20, 0.95, 0)],
        STROKE_COLORS["black"],
    )
    # Right side of the loop, top -> bottom
    strokes["red"] = smooth(
        [(0.20, 0.95, 0), (0.50, 0.60, 0), (0.55, 0.15, 0), (0.40, -0.05, 0)],
        STROKE_COLORS["red"],
    )
    # Left side of the loop, bottom -> top
    strokes["lightblue"] = smooth(
        [(-0.40, -0.05, 0), (-0.55, 0.15, 0), (-0.50, 0.60, 0), (-0.20, 0.95, 0)],
        STROKE_COLORS["lightblue"],
    )
    # Bottom of the loop, right -> left
    strokes["purple"] = smooth(
        [(0.40, -0.05, 0), (0.15, -0.12, 0), (-0.15, -0.15, 0), (-0.40, -0.05, 0)],
        STROKE_COLORS["purple"],
    )
    # Junction connecting loop to tail
    strokes["orange"] = smooth(
        [(0.40, -0.05, 0), (0.35, -0.25, 0)],
        STROKE_COLORS["orange"],
    )
    # Descending tail
    strokes["brown"] = smooth(
        [(0.35, -0.25, 0), (0.10, -0.60, 0), (-0.15, -1.00, 0)],
        STROKE_COLORS["brown"],
    )
    return strokes


def make_icon(stroke_names, strokes_def, max_size=0.35, stroke_width=3.5):
    """A small icon containing the named strokes.

    Sized so the icon's bounding box (in either dimension) fits within
    `max_size`. Scaling uniformly by min(w_ratio, h_ratio) keeps wide-short
    strokes (e.g. the top cap) from blowing up when their tiny height
    is the only thing scaled.
    """
    icon = VGroup()
    for name in stroke_names:
        s = strokes_def[name].copy()
        s.set_stroke(width=stroke_width)
        icon.add(s)
    if len(icon) > 0:
        cur_h = max(icon.height, 1e-6)
        cur_w = max(icon.width, 1e-6)
        icon.scale(min(max_size / cur_h, max_size / cur_w))
    return icon


class Digit9Strokes(Scene):
    def construct(self):
        # --- Title ---
        title = Text("Neurons learn the strokes of a 9", font_size=38, weight=BOLD)
        sub = Text(
            "single strokes  →  combined pieces  →  the whole digit",
            font_size=22, color=GREY_B,
        ).next_to(title, DOWN, buff=0.3)
        self.play(Write(title), FadeIn(sub, shift=UP * 0.2))
        self.wait(1.4)
        self.play(FadeOut(title), FadeOut(sub))

        # --- The colored 9 on the left ---
        strokes_def = make_stroke_defs(stroke_width=10)
        nine = VGroup(*[strokes_def[n] for n in STROKE_NAMES])
        nine.scale(1.4).to_edge(LEFT, buff=0.7).shift(UP * 0.1)

        self.play(
            LaggedStart(
                *[Create(strokes_def[n]) for n in STROKE_NAMES],
                lag_ratio=0.2,
            ),
            run_time=2.2,
        )
        self.wait(0.4)

        # --- Build the network on the right ---
        layers = self.build_layers(n_layers=4, n_per_layer=7)
        net_group = VGroup(*layers)
        net_group.scale(0.78).next_to(nine, RIGHT, buff=0.8).shift(UP * 0.15)

        edge_groups = []
        for L1, L2 in zip(layers[:-1], layers[1:]):
            edges = VGroup()
            for a in L1:
                for b in L2:
                    e = Line(
                        a.get_center(), b.get_center(),
                        stroke_width=0.55, stroke_opacity=0.28,
                        color=GREY_C,
                    )
                    edges.add(e)
            edge_groups.append(edges)

        self.play(
            LaggedStart(*[FadeIn(L) for L in layers], lag_ratio=0.15),
            *[Create(E) for E in edge_groups],
            run_time=1.8,
        )
        self.wait(0.3)

        neuron_icons = {}  # (layer_idx, neuron_idx) -> icon VGroup

        # --- Stage 1: split the 9 into L1 neurons ---
        l1_anims = []
        for ni, stroke_names in enumerate(LAYER_CONTENTS[0]):
            if not stroke_names:
                continue
            name = stroke_names[0]
            target_neuron = layers[0][ni]
            icon = make_icon(
                [name], strokes_def,
                max_size=target_neuron.height * 0.65,
                stroke_width=3.2,
            )
            icon.move_to(target_neuron.get_center())
            neuron_icons[(0, ni)] = icon
            l1_anims.append(TransformFromCopy(strokes_def[name], icon))

        self.play(nine.animate.set_stroke(opacity=0.35), run_time=0.6)
        self.play(LaggedStart(*l1_anims, lag_ratio=0.15), run_time=2.4)
        self.wait(0.5)

        # --- Stages 2–4: progressively combine into L2, L3, L4 ---
        for layer_idx in range(1, 4):
            self.populate_layer(
                layer_idx, layers, edge_groups, neuron_icons, strokes_def,
            )
            self.wait(0.3)

        # --- Final: highlight the assembled-9 neuron in L4 ---
        full_neuron = layers[3][2]
        full_icon = neuron_icons[(3, 2)]
        self.play(
            full_neuron.animate.set_stroke(YELLOW, width=3.5),
            run_time=0.5,
        )
        self.play(Indicate(full_icon, scale_factor=1.4, color=YELLOW), run_time=0.8)

        caption = Text(
            "deep neurons assemble many strokes  →  the whole digit",
            font_size=22, color=GREY_B,
        ).to_edge(DOWN, buff=0.4)
        self.play(FadeIn(caption, shift=UP * 0.2))
        self.wait(2.8)

    # ------------------------------------------------------------ helpers

    def build_layers(self, n_layers, n_per_layer):
        layers = []
        for _ in range(n_layers):
            layer = VGroup()
            for _ in range(n_per_layer):
                c = Circle(
                    radius=0.28, color=GREY_B,
                    stroke_width=2.0, fill_opacity=0.12,
                )
                layer.add(c)
            layer.arrange(DOWN, buff=0.18)
            layers.append(layer)
        VGroup(*layers).arrange(RIGHT, buff=1.0)
        return layers

    def populate_layer(self, layer_idx, layers, edge_groups, neuron_icons, strokes_def):
        """Fill `layers[layer_idx]` neurons with combined icons.

        For each populated target neuron, flash the connecting edges from
        every source neuron that shares a stroke (colored by that stroke).
        """
        source_layer = layers[layer_idx - 1]
        target_layer = layers[layer_idx]
        edges = edge_groups[layer_idx - 1]
        n_target = len(target_layer)

        for ni, target_strokes in enumerate(LAYER_CONTENTS[layer_idx]):
            if not target_strokes:
                continue

            target_neuron = target_layer[ni]
            icon = make_icon(
                target_strokes, strokes_def,
                max_size=target_neuron.height * 0.75,
                stroke_width=2.6,
            )
            icon.move_to(target_neuron.get_center())
            neuron_icons[(layer_idx, ni)] = icon

            edge_flashes = []
            src_pulses = []
            for src_ni, src_strokes in enumerate(LAYER_CONTENTS[layer_idx - 1]):
                shared = [s for s in src_strokes if s in target_strokes]
                if not shared:
                    continue
                flash_color = STROKE_COLORS[shared[0]]
                edge_idx = src_ni * n_target + ni
                if edge_idx < len(edges):
                    edge_flashes.append(
                        ShowPassingFlash(
                            edges[edge_idx].copy().set_stroke(
                                color=flash_color, width=2.6, opacity=1.0,
                            ),
                            time_width=0.5, run_time=0.7,
                        )
                    )
                src_pulses.append(
                    Indicate(
                        source_layer[src_ni],
                        color=YELLOW, scale_factor=1.15, run_time=0.6,
                    )
                )

            self.play(
                *edge_flashes, *src_pulses,
                FadeIn(icon, scale=0.5, run_time=0.7),
            )
