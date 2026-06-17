"""
plotting.py
-----------
Stage 1 visualisation for the CYENS water-meter project.

The point of Stage 1 is to SEE each meter before designing features or
detectors. plot_meter() shows two stacked panels that share a time axis:

  1. Cumulative flow  -- the raw counter; a rising staircase under normal
     operation. A flat leading stretch reveals the pre-connection
     commissioning period (meter powered on but not yet plumbed in).
  2. Hourly consumption -- flow.diff(), i.e. how much water was used each
     hour. This is what we actually care about. Mostly-zero with bursts is
     typical; negative values are counter rollbacks (resets/glitches).

Nothing here cleans the data -- it only visualises it. Trimming the
commissioning period and handling rollbacks happens in Stage 2 (cleaning),
informed by what these plots reveal.
"""

from __future__ import annotations
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd


def _as_timeseries(df: pd.DataFrame) -> pd.Series:
    """Return cumulative flow as a time-indexed Series, whether dataTime is
    the index or a column."""
    if df.index.name == "dataTime":
        s = df["flow"].copy()
    elif "dataTime" in df.columns:
        s = df.set_index("dataTime")["flow"].copy()
    else:
        raise KeyError("Need 'dataTime' as index or column, and a 'flow' column.")
    return s


def first_flow_time(df: pd.DataFrame):
    """The first timestamp at which cumulative flow actually increases --
    i.e. the meter's true start of service (end of commissioning period).
    Returns None if flow never moves."""
    flow = _as_timeseries(df)
    moved = flow.diff() > 0
    if not moved.any():
        return None
    return moved.idxmax()  # first True


def plot_meter(df: pd.DataFrame,
               title: str | None = None,
               show_connection: bool = True,
               mark_rollbacks: bool = True,
               figsize: tuple[int, int] = (13, 6)):
    """
    Plot one meter: cumulative flow (top) and hourly consumption (bottom).

    Parameters
    ----------
    df : DataFrame
        A loaded meter (from load_meter_csv), with 'flow' and a dataTime
        index (or column).
    title : str, optional
        Plot title. If None, uses the deviceId when available.
    show_connection : bool
        Draw a vertical line at the first-flow time (start of service).
    mark_rollbacks : bool
        Highlight hours where the counter decreased (negative consumption).
    figsize : tuple
        Figure size.

    Returns
    -------
    fig, (ax_flow, ax_cons) : the Matplotlib figure and the two axes,
        so the caller can further customise or save if desired.
    """
    flow = _as_timeseries(df).sort_index()
    consumption = flow.diff()  # hourly usage; first value is NaN

    # Resolve a title
    if title is None:
        if "deviceId" in df.columns and len(df):
            title = f"Meter {df['deviceId'].iloc[0]}"
        else:
            title = "Meter"

    fig, (ax_flow, ax_cons) = plt.subplots(
        2, 1, figsize=figsize, sharex=True,
        gridspec_kw={"height_ratios": [1, 2]},
    )

    # --- Top panel: cumulative flow (the raw counter) ---
    ax_flow.plot(flow.index, flow.values, lw=0.9, color="#1f6feb")
    ax_flow.set_ylabel("Cumulative\nflow (m³)")
    ax_flow.set_title(title, loc="left", fontsize=12, fontweight="bold")
    ax_flow.grid(True, alpha=0.25)

    # --- Bottom panel: hourly consumption (what we care about) ---
    ax_cons.plot(consumption.index, consumption.values,
                 lw=0.7, color="#2f6f3e")
    ax_cons.axhline(0, color="grey", lw=0.6, alpha=0.6)
    ax_cons.set_ylabel("Hourly\nconsumption (m³)")
    ax_cons.set_xlabel("Time")
    ax_cons.grid(True, alpha=0.25)

    # Mark counter rollbacks (negative consumption) in red
    if mark_rollbacks:
        roll = consumption[consumption < 0]
        if len(roll):
            ax_cons.scatter(roll.index, roll.values, color="#d1242f",
                            s=18, zorder=5,
                            label=f"rollbacks ({len(roll)})")
            ax_cons.legend(loc="upper right", fontsize=9)

    # Mark the connection point (first real flow) on both panels
    if show_connection:
        conn = first_flow_time(df)
        if conn is not None:
            for ax in (ax_flow, ax_cons):
                ax.axvline(conn, color="#a371f7", ls="--", lw=1.1, alpha=0.8)
            ax_flow.text(conn, ax_flow.get_ylim()[1], " first flow",
                         color="#a371f7", va="top", ha="left", fontsize=9)

    # Tidy the date axis
    ax_cons.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax_cons.xaxis.set_major_formatter(mdates.ConciseDateFormatter(
        ax_cons.xaxis.get_major_locator()))

    fig.tight_layout()
    return fig, (ax_flow, ax_cons)


if __name__ == "__main__":
    # Manual smoke test:
    #   python src/plotting.py data/raw/meter_xxx.csv
    import sys
    from data_loading import load_meter_csv
    if len(sys.argv) > 1:
        df = load_meter_csv(sys.argv[1])
        fig, _ = plot_meter(df)
        out = "meter_plot.png"
        fig.savefig(out, dpi=110)
        print(f"saved {out}")
    else:
        print("Usage: python src/plotting.py <meter.csv>")