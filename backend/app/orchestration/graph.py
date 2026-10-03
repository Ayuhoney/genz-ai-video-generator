from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.orchestration import nodes
from app.orchestration.checkpointing import get_checkpointer
from app.orchestration.state import WorkflowState
from app.orchestration.transitions import (
    after_assembly_planning,
    after_asset_planning,
    after_audio_planning,
    after_pause_gate,
    after_video_planning,
)


def build_graph() -> StateGraph:
    graph = StateGraph(WorkflowState)

    graph.add_node("director_planning", nodes.director_planning)
    graph.add_node("scene_planning", nodes.scene_planning)
    graph.add_node("shot_planning", nodes.shot_planning)
    graph.add_node("asset_planning", nodes.asset_planning)
    graph.add_node("video_generation_planning", nodes.video_generation_planning)
    graph.add_node("pause_gate", nodes.pause_if_needed)
    graph.add_node("audio_planning", nodes.audio_planning)
    graph.add_node("assembly_planning", nodes.assembly_planning)
    graph.add_node("finalization", nodes.finalization)

    graph.add_edge(START, "director_planning")
    graph.add_edge("director_planning", "scene_planning")
    graph.add_edge("scene_planning", "shot_planning")
    graph.add_edge("shot_planning", "asset_planning")

    graph.add_conditional_edges(
        "asset_planning",
        after_asset_planning,
        {
            "continue": "video_generation_planning",
            "pause_gate": "pause_gate",
            "end_failed": END,
        },
    )
    graph.add_conditional_edges(
        "video_generation_planning",
        after_video_planning,
        {
            "continue": "audio_planning",
            # Capture the next story beat (stills → clips) before audio.
            "next_scene": "asset_planning",
            "pause_gate": "pause_gate",
            "end_failed": END,
        },
    )
    graph.add_conditional_edges(
        "audio_planning",
        after_audio_planning,
        {
            "continue": "assembly_planning",
            "pause_gate": "pause_gate",
            "end_failed": END,
        },
    )
    graph.add_conditional_edges(
        "assembly_planning",
        after_assembly_planning,
        {
            "continue": "finalization",
            "pause_gate": "pause_gate",
            "end_failed": END,
        },
    )
    graph.add_conditional_edges(
        "pause_gate",
        after_pause_gate,
        {
            "retry_asset": "asset_planning",
            "retry_video": "video_generation_planning",
            "retry_audio": "audio_planning",
            "retry_assembly": "assembly_planning",
            "end_failed": END,
        },
    )

    graph.add_edge("finalization", END)
    return graph


def compile_graph(*, checkpointer=None) -> CompiledStateGraph:
    builder = build_graph()
    saver = checkpointer if checkpointer is not None else get_checkpointer()
    return builder.compile(checkpointer=saver)
