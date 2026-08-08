import { ApiStatus } from "@/components/api-status";
import styles from "./page.module.css";

export default function Home() {
  return (
    <main className={styles.main}>
      <h1>Service CRM</h1>
      <p>Single-tenant CRM for local service businesses.</p>
      <ApiStatus />
    </main>
  );
}
