/**
 * Dashboard — post-unseal landing page.
 *
 * Placeholder for the main Conclave Engine UI (Phase 6+).
 * Rendered after the vault is successfully unsealed.
 */

export default function Dashboard() {
  return (
    <main
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        backgroundColor: "var(--color-bg)",
        color: "var(--color-text-primary)",
        fontFamily: "var(--font-family)",
      }}
    >
      <section aria-labelledby="dashboard-heading">
        <h1
          id="dashboard-heading"
          style={{ fontSize: "2rem", fontWeight: 700, marginBottom: "1rem" }}
        >
          Conclave Engine
        </h1>
        <p style={{ color: "var(--color-text-secondary)" }}>
          Vault unsealed. Dashboard coming in Phase 6.
        </p>
      </section>
    </main>
  );
}
