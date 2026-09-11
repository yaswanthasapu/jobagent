import re
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from config.settings import settings
from models.application import ApplicationRecord, ApplicationStatus

from rich.console import Console
from rich.table import Table

Base = declarative_base()

def to_local_datetime(dt: Optional[datetime]) -> Optional[datetime]:
    """Converts a UTC or naive datetime to the local system timezone."""
    if not dt:
        return None
    if dt.tzinfo is None:
        # SQLite stores naive datetimes; JobAgent records all timestamps in UTC.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()

def clean_scraped_text(text: Optional[str]) -> str:
    """
    Cleans scraped titles, company names, and locations by removing duplicate
    multiline text, LinkedIn verification badges, and normalizing unicode characters.
    """
    if not text:
        return ""
    # Normalize unicode quotes and dashes
    text = (
        text.replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2022", "*")
        .replace("\xa0", " ")
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    first_line = lines[0] if lines else text.strip()
    # Remove trailing badge text like "with verification"
    first_line = re.sub(r"\s+with verification$", "", first_line, flags=re.IGNORECASE).strip()
    return re.sub(r"\s+", " ", first_line)

class ApplicationModel(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(100), unique=True, index=True, nullable=False)
    job_title = Column(String(255), nullable=False)
    company = Column(String(255), index=True, nullable=False)
    location = Column(String(255), nullable=False)
    job_url = Column(String(500), index=True, nullable=False)
    platform = Column(String(50), nullable=True, default="LinkedIn")
    match_score = Column(Float, nullable=False)
    applied_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    status = Column(String(50), nullable=False)
    notes = Column(Text, nullable=True)

class DatabaseService:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.DATABASE_URL
        self.engine = create_async_engine(self.db_url, echo=False)
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )

    async def init_db(self) -> None:
        """Create database tables if they do not exist and automatically migrate schema."""
        if getattr(self, "_initialized", False):
            return
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            def _migrate_schema(sync_conn):
                from sqlalchemy import text
                cursor = sync_conn.execute(text("PRAGMA table_info(applications)"))
                existing_cols = {row[1].lower() for row in cursor.fetchall()}
                if "platform" not in existing_cols and len(existing_cols) > 0:
                    sync_conn.execute(text("ALTER TABLE applications ADD COLUMN platform VARCHAR(50) DEFAULT 'LinkedIn'"))
                    sync_conn.execute(text("UPDATE applications SET platform = 'Naukri' WHERE lower(job_url) LIKE '%naukri%'"))
                    sync_conn.execute(text("UPDATE applications SET platform = 'LinkedIn' WHERE platform IS NULL OR platform = ''"))

                # Clean existing records with multiline or badge text
                if len(existing_cols) > 0:
                    rows = sync_conn.execute(text("SELECT id, job_title, company, location FROM applications")).fetchall()
                    for r_id, title, comp, loc in rows:
                        ct = clean_scraped_text(title)
                        cc = clean_scraped_text(comp)
                        cl = clean_scraped_text(loc)
                        if ct != title or cc != comp or cl != loc:
                            sync_conn.execute(
                                text("UPDATE applications SET job_title = :t, company = :c, location = :l WHERE id = :id"),
                                {"t": ct, "c": cc, "l": cl, "id": r_id}
                            )

            await conn.run_sync(_migrate_schema)
        self._initialized = True

    async def is_already_applied(self, job_id: str, job_url: str = "", company: str = "") -> bool:
        """
        Check whether the job has already been processed or applied to.
        Checks by job_id or (job_url + company).
        """
        await self.init_db()
        async with self.session_factory() as session:
            conditions = [ApplicationModel.job_id == str(job_id)]
            if job_url and company:
                clean_url = job_url.split("?")[0].rstrip("/")
                conditions.append(
                    and_(
                        ApplicationModel.job_url.like(f"{clean_url}%"),
                        ApplicationModel.company.ilike(company.strip())
                    )
                )

            stmt = select(ApplicationModel).where(or_(*conditions))
            result = await session.execute(stmt)
            existing = result.scalars().first()
            return existing is not None

    async def record_application(self, record: ApplicationRecord) -> ApplicationRecord:
        """Persist or update an application record."""
        await self.init_db()
        async with self.session_factory() as session:
            async with session.begin():
                stmt = select(ApplicationModel).where(ApplicationModel.job_id == record.job_id)
                res = await session.execute(stmt)
                existing = res.scalars().first()

                effective_platform = record.platform or ("Naukri" if "naukri" in (record.job_url or "").lower() else "LinkedIn")
                clean_title = clean_scraped_text(record.job_title)
                clean_company = clean_scraped_text(record.company)
                clean_location = clean_scraped_text(record.location)
                current_utc = datetime.now(timezone.utc)

                if existing:
                    existing.job_title = clean_title
                    existing.company = clean_company
                    existing.location = clean_location
                    existing.job_url = record.job_url
                    existing.platform = effective_platform
                    existing.match_score = record.match_score
                    if record.status in [ApplicationStatus.SUBMITTED, ApplicationStatus.APPLIED, ApplicationStatus.DRY_RUN_PASSED]:
                        existing.applied_at = record.applied_at or current_utc
                    else:
                        existing.applied_at = record.applied_at or existing.applied_at or current_utc
                    existing.status = record.status.value
                    existing.notes = record.notes
                    await session.flush()
                    record.id = existing.id
                else:
                    db_obj = ApplicationModel(
                        job_id=record.job_id,
                        job_title=clean_title,
                        company=clean_company,
                        location=clean_location,
                        job_url=record.job_url,
                        platform=effective_platform,
                        match_score=record.match_score,
                        applied_at=record.applied_at or current_utc,
                        status=record.status.value,
                        notes=record.notes
                    )
                    session.add(db_obj)
                    await session.flush()
                    record.id = db_obj.id

            return record

    async def get_recent_applications(self, limit: int = 50) -> List[ApplicationRecord]:
        await self.init_db()
        async with self.session_factory() as session:
            stmt = (
                select(ApplicationModel)
                .order_by(ApplicationModel.applied_at.desc(), ApplicationModel.id.desc())
                .limit(limit)
            )
            res = await session.execute(stmt)
            records = []
            for item in res.scalars().all():
                plat = getattr(item, "platform", None) or ("Naukri" if "naukri" in (item.job_url or "").lower() else "LinkedIn")
                records.append(
                    ApplicationRecord(
                        id=item.id,
                        job_id=item.job_id,
                        job_title=clean_scraped_text(item.job_title),
                        company=clean_scraped_text(item.company),
                        location=clean_scraped_text(item.location),
                        job_url=item.job_url,
                        platform=plat,
                        match_score=item.match_score,
                        applied_at=item.applied_at,
                        status=ApplicationStatus(item.status),
                        notes=item.notes
                    )
                )
            return records

    async def get_all_applied_jobs(self, limit: int = 100) -> List[ApplicationRecord]:
        """
        Retrieves all jobs that were applied to (SUBMITTED, APPLIED, or DRY_RUN_PASSED),
        ordered chronologically by applied_at desc, id desc.
        """
        await self.init_db()
        async with self.session_factory() as session:
            stmt = (
                select(ApplicationModel)
                .where(
                    ApplicationModel.status.in_([
                        ApplicationStatus.SUBMITTED.value,
                        ApplicationStatus.APPLIED.value,
                        ApplicationStatus.DRY_RUN_PASSED.value
                    ])
                )
                .order_by(ApplicationModel.applied_at.desc(), ApplicationModel.id.desc())
                .limit(limit)
            )
            res = await session.execute(stmt)
            records = []
            for item in res.scalars().all():
                plat = getattr(item, "platform", None) or ("Naukri" if "naukri" in (item.job_url or "").lower() else "LinkedIn")
                records.append(
                    ApplicationRecord(
                        id=item.id,
                        job_id=item.job_id,
                        job_title=clean_scraped_text(item.job_title),
                        company=clean_scraped_text(item.company),
                        location=clean_scraped_text(item.location),
                        job_url=item.job_url,
                        platform=plat,
                        match_score=item.match_score,
                        applied_at=item.applied_at,
                        status=ApplicationStatus(item.status),
                        notes=item.notes
                    )
                )
            return records

    async def display_applied_jobs_table(self, console: Optional[Console] = None, limit: int = 100) -> None:
        """
        Renders a rich formatted table displaying all applied jobs till now.
        Timestamps are automatically converted to local system timezone.
        """
        if console is None:
            console = Console()

        await self.init_db()
        records = await self.get_all_applied_jobs(limit=limit)

        if not records:
            console.print("\n[yellow]No applied jobs recorded in the database yet.[/yellow]")
            console.print("[dim]Run an application session (Option [2] or /apply in chat) to start applying to jobs![/dim]\n")
            return

        # Responsive layout: compact for standard 80-col terminals, full for wide windows
        is_compact = (console.width or 80) < 105

        table = Table(
            title=f"[bold cyan]Jobs Applied Till Now ({len(records)} Total)[/bold cyan]",
            header_style="bold cyan",
            border_style="dim",
            expand=False
        )
        table.add_column("#", style="dim", justify="right", width=2)
        table.add_column("Platform", style="bold", width=8)
        table.add_column("Job Title", style="bold white", max_width=16 if is_compact else 26, overflow="ellipsis", no_wrap=True)
        table.add_column("Company", style="cyan", max_width=12 if is_compact else 18, overflow="ellipsis", no_wrap=True)
        if not is_compact:
            table.add_column("Location", style="white", max_width=16, overflow="ellipsis", no_wrap=True)
        table.add_column("Match", style="green", justify="right", width=5)
        table.add_column("Date Applied", style="bold yellow", width=16, no_wrap=True)
        table.add_column("Status", style="bold", width=9, no_wrap=True)
        if not is_compact:
            table.add_column("Job Link / URL", style="blue", overflow="ellipsis")

        li_count = 0
        naukri_count = 0

        for i, rec in enumerate(records, 1):
            plat_name = rec.platform or ("Naukri" if "naukri" in rec.job_url.lower() else "LinkedIn")
            if "naukri" in plat_name.lower():
                plat_styled = "[bold green]Naukri[/bold green]"
                naukri_count += 1
            else:
                plat_styled = "[bold blue]LinkedIn[/bold blue]"
                li_count += 1

            status_style = "green" if rec.status in [ApplicationStatus.SUBMITTED, ApplicationStatus.APPLIED] else "yellow"
            status_text = f"[{status_style}]{rec.status.value}[/{status_style}]"
            local_dt = to_local_datetime(rec.applied_at)
            date_format = "%d %b %H:%M" if is_compact else "%Y-%m-%d %H:%M"
            date_str = local_dt.strftime(date_format) if local_dt else "Recent"
            clean_t = clean_scraped_text(rec.job_title)
            clean_c = clean_scraped_text(rec.company)
            clean_l = clean_scraped_text(rec.location)

            row = [
                str(i),
                plat_styled,
                clean_t,
                clean_c,
            ]
            if not is_compact:
                row.append(clean_l)
            row.extend([
                f"{int(rec.match_score)}%",
                date_str,
                status_text,
            ])
            if not is_compact:
                row.append(rec.job_url)

            table.add_row(*row)

        local_tz = datetime.now().astimezone().tzname() or "Local Time"
        console.print("\n")
        console.print(table)
        console.print(f"[dim]Summary: Total Applied: [bold green]{len(records)}[/bold green] | LinkedIn: [bold blue]{li_count}[/bold blue] | Naukri: [bold green]{naukri_count}[/bold green] | Timestamps displayed in {local_tz}.[/dim]\n")

    async def close(self) -> None:
        await self.engine.dispose()

