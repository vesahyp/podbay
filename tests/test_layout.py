from podbay.layout import Placement, Screen, desktops_needed, half_frames, plan_layout


def test_half_frames_even_width_no_gap_no_overlap():
    left, right = half_frames((0, 0, 100, 50))
    assert left == (0, 0, 50, 50)
    assert right == (50, 0, 100, 50)
    # No gap, no overlap: the halves exactly tile the frame.
    assert left[2] == right[0]
    assert left[0] == 0 and right[2] == 100


def test_half_frames_odd_width_right_half_absorbs_extra_pixel():
    left, right = half_frames((0, 0, 101, 50))
    assert left == (0, 0, 50, 50)
    assert right == (50, 0, 101, 50)
    assert (left[2] - left[0]) == 50
    assert (right[2] - right[0]) == 51


def test_half_frames_accepts_screen_dataclass():
    left, right = half_frames(Screen(-1720, -1410, 1720, 0))
    assert left == (-1720, -1410, 0, 0)
    assert right == (0, -1410, 1720, 0)


def test_half_frames_negative_y_external_frame_preserved():
    frame = Screen(-1720, -1410, 1720, 0)
    left, right = half_frames(frame)
    for bounds in (left, right):
        assert bounds[1] == -1410
        assert bounds[3] == 0


def test_desktops_needed_even_count():
    assert desktops_needed(12) == 6


def test_desktops_needed_odd_count():
    assert desktops_needed(5) == 3


def test_desktops_needed_zero_and_negative():
    assert desktops_needed(0) == 0
    assert desktops_needed(-3) == 0


def test_desktops_needed_single_window():
    assert desktops_needed(1) == 1


def test_plan_layout_even_count_pairs_left_right_per_desktop():
    frame = (0, 0, 100, 50)
    plan = plan_layout(["a", "b", "c", "d"], frame)
    assert [p.pane_id for p in plan] == ["a", "b", "c", "d"]
    assert [p.desktop_index for p in plan] == [0, 0, 1, 1]
    assert [p.slot for p in plan] == ["left", "right", "left", "right"]
    left, right = half_frames(frame)
    assert plan[0].bounds == left
    assert plan[1].bounds == right
    assert plan[2].bounds == left
    assert plan[3].bounds == right


def test_plan_layout_odd_count_last_desktop_gets_left_half_only():
    frame = (0, 0, 100, 50)
    plan = plan_layout(["a", "b", "c"], frame)
    assert plan[2].desktop_index == 1
    assert plan[2].slot == "left"
    left, _right = half_frames(frame)
    # The lone window on the last desktop keeps the left half, never a
    # full-width stretch.
    assert plan[2].bounds == left
    assert (plan[2].bounds[2] - plan[2].bounds[0]) == (100 - 0) // 2


def test_plan_layout_single_window_gets_left_half_of_its_own_desktop():
    frame = (0, 0, 100, 50)
    plan = plan_layout(["only"], frame)
    assert len(plan) == 1
    assert plan[0].desktop_index == 0
    assert plan[0].slot == "left"
    left, _right = half_frames(frame)
    assert plan[0].bounds == left


def test_plan_layout_preserves_selection_order():
    frame = (0, 0, 100, 50)
    plan = plan_layout(["z", "y", "x"], frame)
    assert [p.pane_id for p in plan] == ["z", "y", "x"]


def test_plan_layout_uses_negative_y_external_frame():
    frame = Screen(-1720, -1410, 1720, 0)
    plan = plan_layout(["a", "b"], frame)
    for placement in plan:
        assert placement.bounds[1] == -1410
        assert placement.bounds[3] == 0
    assert plan[0].bounds[0] == -1720
    assert plan[1].bounds[2] == 1720


def test_plan_layout_returns_placement_instances():
    plan = plan_layout(["a"], (0, 0, 100, 50))
    assert isinstance(plan[0], Placement)
