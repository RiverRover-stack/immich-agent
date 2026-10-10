# Frontend third-party software

Versions are pinned by package-lock.json. No images, fonts or external data are downloaded at runtime.

| Package | Purpose | License |
| --- | --- | --- |
| React, React DOM, react-is | UI rendering | MIT |
| TypeScript | Static typing | Apache-2.0 |
| Vite, @vitejs/plugin-react | Build and development server | MIT |
| Tailwind CSS, @tailwindcss/vite | Styling pipeline | MIT |
| @xyflow/react | Interactive dependency graph | MIT |
| Recharts | Telemetry charts | MIT |
| lucide-react | Interface icons | ISC |
| Vitest | Workflow regression tests | MIT |
| @playwright/test | Browser validation | Apache-2.0 |
| @types/react, @types/react-dom | Type definitions | MIT |

The demo graph is informed by the existing local Compose topology, including its model-cache volume. All logs, metrics, test results and patches are synthetic. Immich application data and credentials are neither bundled nor committed by this frontend.
