import { useParams } from "react-router-dom";

import { Card, PageHeader } from "../components/ui";

/** Table editor (the drawing canvas comes in the next step; until then the OpenCV tool is used). */
export default function TableSetupPage() {
  const { videoId } = useParams();
  const command = `python backend/scripts/draw_tables.py ${videoId ?? "<video id>"}`;
  return (
    <div className="space-y-6">
      <PageHeader title="Table Setup" subtitle="Draw where the tables are, once per camera view." />
      <Card className="space-y-3 px-5 py-5 text-sm text-slate-600">
        <p>
          The in-browser table editor is not available yet. While the video is streaming, draw the tables with the
          desktop tool from the project folder:
        </p>
        <pre className="overflow-x-auto rounded-lg bg-slate-900 px-4 py-3 text-xs text-emerald-300">{command}</pre>
        <p>The Live Monitor picks up the saved tables the next time the stream is started.</p>
      </Card>
    </div>
  );
}
