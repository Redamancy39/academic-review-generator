# Exports API - export literature to various formats
from datetime import datetime
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ...core.models import PaperRecord
from ...services.literature_exporter import LiteratureExporter
from .reviews import tasks_storage

router = APIRouter(prefix="/exports", tags=["exports"])

# Create exporter instance
exporter = LiteratureExporter()


@router.get("/{task_id}/papers.xlsx")
async def export_papers_xlsx(task_id: str) -> Response:
    """Export papers for a task as Excel xlsx file.

    Args:
        task_id: The task ID.

    Returns:
        Excel file with paper records.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]

    # Get records from task results
    raw_records = task.get("raw_records", [])
    selected_records = task.get("selected_records", [])

    # Combine all records, mark selected ones
    all_records_dict: Dict[str, Any] = {}
    for r in raw_records:
        record = PaperRecord(**r) if isinstance(r, dict) else r
        all_records_dict[record.ref_id] = record

    # Mark selected records
    selected_ref_ids = set()
    for r in selected_records:
        ref_id = r.get("ref_id") if isinstance(r, dict) else r.ref_id
        selected_ref_ids.add(ref_id)

    # Build combined list
    all_records = list(all_records_dict.values())

    # Export to xlsx
    try:
        xlsx_bytes = exporter.export_to_xlsx(
            records=all_records,
            task_id=task_id,
            topic=task.get("topic", ""),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"papers_{task_id[:8]}_{timestamp}.xlsx"

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/{task_id}/selected.xlsx")
async def export_selected_papers_xlsx(task_id: str) -> Response:
    """Export selected papers for a task as Excel xlsx file.

    Args:
        task_id: The task ID.

    Returns:
        Excel file with selected paper records.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]
    selected_records = task.get("selected_records", [])

    if not selected_records:
        raise HTTPException(status_code=404, detail="No selected records found for this task.")

    # Convert to PaperRecord objects
    records = [
        PaperRecord(**r) if isinstance(r, dict) else r
        for r in selected_records
    ]

    # Export to xlsx
    try:
        xlsx_bytes = exporter.export_to_xlsx(
            records=records,
            task_id=task_id,
            topic=task.get("topic", ""),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"selected_papers_{task_id[:8]}_{timestamp}.xlsx"

    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/{task_id}/papers.bib")
async def export_papers_bibtex(task_id: str) -> Response:
    """Export papers for a task as BibTeX format.

    Args:
        task_id: The task ID.

    Returns:
        BibTeX file with paper records.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]
    selected_records = task.get("selected_records", [])

    if not selected_records:
        raise HTTPException(status_code=404, detail="No selected records found for this task.")

    # Convert to PaperRecord objects
    records = [
        PaperRecord(**r) if isinstance(r, dict) else r
        for r in selected_records
    ]

    bibtex_str = exporter.export_to_bibtex(records)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"references_{task_id[:8]}_{timestamp}.bib"

    return Response(
        content=bibtex_str,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/{task_id}/papers.csv")
async def export_papers_csv(task_id: str) -> Response:
    """Export papers for a task as CSV format.

    Args:
        task_id: The task ID.

    Returns:
        CSV file with paper records.
    """
    if task_id not in tasks_storage:
        raise HTTPException(status_code=404, detail="Task not found.")

    task = tasks_storage[task_id]
    selected_records = task.get("selected_records", [])

    if not selected_records:
        raise HTTPException(status_code=404, detail="No selected records found for this task.")

    # Convert to PaperRecord objects
    records = [
        PaperRecord(**r) if isinstance(r, dict) else r
        for r in selected_records
    ]

    csv_str = exporter.export_to_csv(
        records=records,
        task_id=task_id,
        topic=task.get("topic", ""),
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"papers_{task_id[:8]}_{timestamp}.csv"

    return Response(
        content=csv_str.encode("utf-8-sig"),  # BOM for Excel compatibility
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
