/** Photos is being rebuilt as its own task (rv-7): the old grid and editor did not work, so they were not carried over. */
export function Photos() {
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Photos</h1>
        <p className="mt-1 text-sm text-muted">Browse photos and edit their location, credit and keywords.</p>
      </div>
      <div className="card flex flex-col items-center gap-2 px-6 py-12 text-center">
        <p className="font-medium">Being rebuilt</p>
        <p className="max-w-md text-sm text-muted">
          The photo grid and editor did not work in the old interface, so they were not carried over. They are being rebuilt
          as their own task (rv-7 in docs/specs/revamp). Drives, tasks, cards and settings all work here.
        </p>
        <a href="#/" className="btn mt-3">
          Back to tasks
        </a>
      </div>
    </div>
  );
}
