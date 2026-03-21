# **Software Architecture Document: Air-Gapped Synthetic Data Generation Engine**

> **HISTORICAL — ARCHIVED**: This document is superseded. Retained for project history only.


## **Executive Architecture Summary**

The proliferation of stringent data privacy regulations, including the General Data Protection Regulation (GDPR), the Health Insurance Portability and Accountability Act (HIPAA), and the Payment Card Industry Data Security Standard (PCI DSS), presents a profound and persistent challenge for enterprise software development. Data engineering and artificial intelligence experimentation require high-fidelity, production-like datasets in lower environments to ensure accurate testing, modeling, and validation. However, the exposure of sensitive Personally Identifiable Information (PII) or Protected Health Information (PHI) outside of highly secure, audited production enclaves carries catastrophic legal, financial, and reputational risks. The proposed platform represents a highly rigorous, "Bring Your Own Compute" (BYOC) synthetic data generation engine designed specifically to resolve this tension for the mid-market enterprise.

Operating under a strict local-only, air-gapped paradigm, the architecture guarantees absolute zero data exfiltration while yielding mock datasets that are structurally, relationally, and statistically identical to production sources. This BYOC philosophy ensures that the enterprise maintains sovereign control over its hardware and network boundaries. Whether the system is deployed on a single developer's isolated workstation, an on-premises enterprise cluster, or a multi-node home server rack designed for local AI experimentation, the architecture functions entirely within a sealed perimeter. No component within the technology stack relies on external cloud compute, software-as-a-service (SaaS) APIs, or outbound telemetry.

The architecture integrates a highly curated, fully open-source technology stack governed exclusively by permissive licensing models. The data ingestion phase is powered by Meltano, utilizing the Singer specification to extract raw source data.1 This extracted data is funneled directly into a strictly ephemeral, in-memory profiling layer managed by DuckDB.3 Within this localized compute enclave, the platform derives statistical distributions, schema definitions, covariance matrices, and relational constraints without writing plaintext sensitive data to persistent disk storage.4 Once the statistical profile is established and safely exported as a metadata artifact, the original source data is subjected to aggressive cryptographic memory erasure protocols to eliminate the risk of data remanence.6

The generation phase employs a bifurcated approach to handle the complexities of modern enterprise data. Structured tabular data—such as financial transaction amounts, demographic categories, and temporal markers—is synthesized using the Python runtime (versions 3.11 or 3.12). This layer leverages the Faker library alongside SciPy and NumPy to replicate complex mathematical distributions.8 Unstructured data, including free-text fields containing simulated human narratives, medical notes, or customer support transcripts, is generated via localized Large Language Models (LLMs) served by Ollama.10 This ensures that no external API calls are executed to commercial AI providers, rigidly maintaining the integrity of the air-gapped boundary.12

Following the generation of the synthetic records, the datasets are transformed and validated using dbt and the dbt-expectations extension.14 This validation layer acts as a mathematical gatekeeper, ensuring that the output data strictly conforms to the statistical expectations established during the profiling phase.16 The entire lifecycle—from initial extraction to final validation—is orchestrated by Dagster, which provides an asset-centric paradigm perfectly suited for deterministic, reproducible pipeline execution.17

Designed specifically for a deeply modular, solo-developer lifecycle, the system allows individual components to be engineered, tested, and deployed in isolated atomic sessions. Security is not treated as a peripheral feature but is intrinsically woven into the fabric of the architecture. The design addresses OWASP Top 10 vulnerabilities directly, mitigates data remanence risks at the operating system level, and systematically neutralizes dependency supply chain threats. The resulting platform delivers an uncompromising balance of data utility, operational autonomy, and cryptographic security.

## **Core Architectural Principles & Engineering Constraints**

The architectural vision of this platform is governed by a series of immutable constraints that dictate every component selection, data flow pathway, and operational procedure. These principles ensure that the platform remains uncompromisingly secure, legally compliant, and highly autonomous under the stewardship of lean engineering teams.

The paramount directive of this platform is the absolute prevention of data exfiltration. Because the source datasets are presumed to contain highly sensitive PII and PHI, the engine must operate as a hermetically sealed system. No component within the stack is permitted to initiate outbound network connections during execution. The deployment model assumes a fully air-gapped environment. All dependencies, container images, Python packages, and quantized language models must be pre-fetched, audited, and securely transported across the air-gap prior to pipeline execution.18 This operational reality mandates rigorous offline package management, utilizing pre-compiled Python wheels and local module registries, effectively neutralizing the risk of arbitrary code execution stemming from dynamic dependency resolution.21

The legal and compliance framework of the architecture demands strict adherence to permissive open-source software (OSS) licenses. Mid-market enterprises frequently operate under complex vendor agreements and intellectual property protections. The platform must be completely unencumbered by copyleft obligations, which would otherwise force the enterprise to disclose proprietary configurations, schemas, or extended source code to the public domain. Consequently, the architecture exclusively permits dependencies licensed under the MIT License, the Apache License 2.0, or the Berkeley Software Distribution (BSD) variants.8 Any component, plugin, or sub-dependency carrying the GNU General Public License (GPL) or the Affero General Public License (AGPL) is explicitly classified as architectural contraband and systematically blocked from the build environment.24 This requirement necessitates profound vigilance and continuous auditing, particularly within the fragmented ecosystem of data integration connectors.

Furthermore, stability and determinism categorically supersede novelty in the selection of all underlying technologies. The platform is engineered to function autonomously under the stewardship of a single developer or a highly constrained engineering pod. Therefore, the technology stack completely avoids alpha-stage libraries, experimental frameworks, and poorly maintained community forks in favor of battle-tested, heavily documented industry standards. This deeply modular design philosophy requires that every phase of the pipeline operates within strict input-output contracts. By decoupling the architecture, the solo developer can construct, validate, and troubleshoot isolated architectural modules—such as the ingestion logic or the statistical validation checks—without requiring the instantiation of the entire systemic whole, thereby preserving operational momentum and cognitive focus.

## **System Architecture & Air-Gapped Data Flow**

The operational lifecycle of the synthetic data generation engine is divided into five distinct chronological phases: Extraction, Statistical Profiling, Cryptographic Erasure, Synthesis, and Validation. The architecture ensures that sensitive, plaintext data exists solely within the first two phases, confined entirely to volatile memory or strongly encrypted ephemeral file systems that are destroyed prior to the generation of the synthetic output.

During the Extraction phase, Meltano initiates the pipeline by invoking a Singer tap tailored to the specific source database.1 To comply with the air-gapped deployment constraint, Meltano is configured to install plugins from local directory paths rather than querying the public Python Package Index (PyPI).21 The extracted data flows through local memory buffers directly into DuckDB.27 DuckDB serves as the localized compute and profiling engine. Configured to operate exclusively in-memory, DuckDB aggregates the raw data to determine critical statistical properties—such as the mean, median, standard deviation, null rates, and categorical cardinality—without persisting the raw records to permanent magnetic or solid-state disk storage.5

Once the statistical profile is accurately generated and securely exported as a metadata schema, the Cryptographic Erasure phase begins. Because Python strings are immutable and rely on unpredictable garbage collection cycles, simply deleting references to the raw data variables is entirely insufficient to prevent memory-scraping attacks.29 The system invokes OS-level memory wiping and aggressive buffer overwriting to ensure all original PII is demonstrably purged from the host machine's Random Access Memory (RAM).30 If the volume of the dataset necessitates disk spilling during the DuckDB profiling phase, DuckDB is constrained to write its temporary files solely to a localized tmpfs partition, which is a RAM-based file system.4 This tmpfs volume is then subjected to a multi-pass secure deletion utility, ensuring that no data remanence survives the operation.7

Following the verified secure destruction of the source data, the Synthesis phase initiates. The Python runtime ingests the sanitized statistical metadata. Using the Faker library for localized discrete data generation and the SciPy/NumPy stack for continuous numerical distributions, the system builds a structurally identical mock dataset in memory.8 For unstructured fields, the pipeline invokes Ollama.10 Ollama runs a localized Large Language Model, generating contextually appropriate unstructured text based strictly on sanitized prompt templates stored within the secure perimeter.32 Ollama's default telemetry and update polling services are forcibly disabled via environment variables to maintain the integrity of the air-gap.12

Finally, the Validation phase utilizes dbt and dbt-expectations. The newly synthesized dataset is loaded into a fresh, isolated DuckDB instance.27 dbt compiles SQL validation tests to ensure that the synthetic data adheres to the structural rules, foreign key constraints, and statistical tolerances defined in the original metadata profile.15 If the synthetic data passes all assertions, it is materialized and exported to the target destination environment.

Code snippet

graph TD  
    subgraph Air-Gapped Secure Perimeter \[Air-Gapped Enterprise Perimeter\]  
          
        subgraph Source Environment  
            SRC  
        end

        subgraph Orchestration Layer  
            DAG  
        end

        subgraph Extraction & Profiling Phase  
            MELT  
            DDB\_PROF  
            TMPFS  
            SHRED((Cryptographic\\nShred & Teardown))  
        end

        subgraph Synthesis Phase  
            META{Sanitized Statistical\\nMetadata Profile}  
            PY\_GEN  
            LLM\[Ollama Local Inference\\nUnstructured Generation\]  
        end

        subgraph Validation & Materialization Phase  
            DBT  
            DBT\_EXP  
            DDB\_SYNC  
            DEST  
        end

        %% Data Flow Connections  
        SRC \-- "Raw Data Stream" \--\> MELT  
        MELT \-- "Standardized JSON" \--\> DDB\_PROF  
        DDB\_PROF \-. "Disk Spill (If memory exceeded)".-\> TMPFS  
        DDB\_PROF \-- "Extract Aggregates" \--\> META  
        DDB\_PROF \-. "Invoke Purge Signal".-\> SHRED  
        TMPFS \-. "Overwrite/Unlink".-\> SHRED

        META \-- "Schema & Bounds" \--\> PY\_GEN  
        META \-- "Context Prompts" \--\> LLM  
        PY\_GEN \-- "Structured Tabular Data" \--\> DDB\_SYNC  
        LLM \-- "Unstructured NLP Data" \--\> DDB\_SYNC

        DDB\_SYNC \-- "Raw Synthetic Tables" \--\> DBT  
        DBT \-- "Run Assertions" \--\> DBT\_EXP  
        DBT\_EXP \-- "Pass/Fail Signal" \--\> DAG  
        DBT \-- "Materialized Output" \--\> DEST

        %% Orchestration Links  
        DAG \=== MELT  
        DAG \=== DDB\_PROF  
        DAG \=== PY\_GEN  
        DAG \=== LLM  
        DAG \=== DBT  
    end

    %% Security Boundaries  
    classDef secure fill:\#f9d0c4,stroke:\#e74c3c,stroke-width:2px;  
    classDef safe fill:\#d4efdf,stroke:\#27ae60,stroke-width:2px;  
    classDef compute fill:\#d6eaf8,stroke:\#2980b9,stroke-width:2px;  
    classDef orch fill:\#fcf3cf,stroke:\#f1c40f,stroke-width:2px;

    class SRC,MELT,DDB\_PROF,TMPFS secure;  
    class PY\_GEN,LLM,DDB\_SYNC compute;  
    class DBT,DBT\_EXP,DEST safe;  
    class DAG orch;

## **Orchestration Selection: The Dagster Paradigm**

The choice of orchestrator is a critical architectural decision for a platform designed to be operated autonomously by a solo developer while maintaining enterprise-grade determinism and security. The evaluation strictly considered the two leading modern Python-native orchestration frameworks: Prefect and Dagster.17 While Prefect offers exceptional operational simplicity and highly dynamic task execution, Dagster is structurally mandated for this specific architecture due to its paradigm of Software-Defined Assets (SDAs), its robust integration with the dbt ecosystem, and its superior localized execution model.17

A fundamental divergence between the two orchestrators lies in their underlying philosophies. Prefect operates on a task-centric model, where the orchestrator primarily monitors the success, failure, and state transitions of arbitrary compute functions.17 While highly flexible, this model decouples the orchestration logic from the actual data being processed. Conversely, Dagster treats data workflows as first-class citizens by tracking the physical data assets produced by the pipeline—such as the intermediate statistical profile, the synthetic tables, and the final validation results.17 This asset-centric approach inherently aligns with the goal of data generation. It allows the solo developer to observe the data lineage directly within the graphical interface. If the validation step fails, the developer can trace the mathematical error back through the asset graph to determine precisely whether the anomaly originated in the Python structured generator or the Ollama unstructured text generator, drastically reducing debugging time.17

The integration capabilities of Dagster further cement its position as the required orchestrator. Dagster features unparalleled, native integration with dbt.35 By utilizing the dagster-dbt library, Dagster automatically parses the underlying dbt project and translates every individual dbt model, seed file, and expectation test into an individual Software-Defined Asset.35 This architectural alignment allows the developer to trigger validation runs on specific, atomic subsets of the synthetic data without executing the entire pipeline from scratch, profoundly increasing iteration speed during single-session development intervals.35

Security boundaries and execution isolation within Dagster provide additional defense-in-depth mechanisms highly relevant to the air-gapped BYOC requirement. Dagster utilizes a multi-process architecture that strictly isolates the webserver from the user code environments.37 The dagster-webserver process parses metadata about the pipeline to render the user interface but does not execute the pipeline code itself. The user code executes in distinct, isolated environments.37 This separation of concerns establishes a formidable defensive boundary: should an adversary manage to poison the statistical metadata or the prompt configurations with a malicious injection payload, the compromised execution remains trapped within the isolated user code process, preventing lateral movement into the orchestration control plane.

Finally, Dagster excels in local, offline development environments. It permits the rapid spin-up of a fully functional user interface and scheduling daemon via the dg dev command without requiring distributed Kubernetes infrastructure, outbound cloud connectivity, or persistent external state databases.38 This native support for ephemeral, localized execution is an absolute necessity for compliance with the air-gapped security mandate.39

| Orchestration Capability | Prefect | Dagster | Architectural Alignment |
| :---- | :---- | :---- | :---- |
| **Primary Paradigm** | Task-centric execution | Asset-centric data lineage | **Dagster**: Aligns with the need to trace statistical anomalies back to specific generation assets.17 |
| **dbt Integration** | Task-level wrapping | Model-level asset mapping | **Dagster**: Translates individual dbt-expectations into observable pipeline nodes.35 |
| **Execution Architecture** | Hybrid (Cloud Control Plane) | Fully localized multi-process | **Dagster**: Supports strict air-gapping with dg dev and isolated code locations.37 |
| **Local Developer Ergonomics** | Highly dynamic, easy syntax | Deep UI, robust testing framework | **Dagster**: Superior debugging UI and testing capabilities for solo engineers.41 |

## **Component Deep Dives & Security Integration**

The architecture mandates strict operational procedures for every tool in the stack to mitigate vulnerabilities, particularly those outlined in the Open Worldwide Application Security Project (OWASP) Top 10\. The platform's resilience relies on the careful configuration of each layer to prevent data leakage, arbitrary code execution, and resource exhaustion. The following sections detail the specific technical implementations required for each layer of the platform.

### **Ingestion & The Copyleft Licensing Trap (Meltano)**

Meltano acts as the primary ingestion gateway, orchestrating Singer taps to extract source data from highly sensitive production replicas. Meltano itself is licensed under the MIT license, making the core framework fully compliant with enterprise mandates.1 However, the Singer ecosystem presents a severe and often overlooked supply-chain licensing risk.

Many of the most robust, battle-tested, and widely deployed Singer taps—specifically those historically maintained by PipelineWise (such as tap-mysql and tap-postgres)—are licensed under the Affero General Public License (AGPL) Version 3\.43 The AGPL is a highly restrictive copyleft license designed specifically to close the "application service provider loophole." Integrating an AGPL-licensed component into this architecture would forcefully extend the copyleft obligations to the entire proprietary configuration, the orchestration code, and potentially the surrounding enterprise execution environment.24 For a mid-market enterprise, this represents a catastrophic legal outcome.

To mitigate this severe licensing vulnerability, the architecture strictly mandates the use of MeltanoLabs variants for all database extractions. The MeltanoLabs tap-postgres and tap-mysql connectors are explicitly licensed under the Apache 2.0 and MIT licenses, successfully circumventing the copyleft contamination risk while maintaining high extraction fidelity.23

Furthermore, because the environment is strictly air-gapped, Meltano cannot dynamically resolve and download these dependencies from the public PyPI servers during pipeline initialization. The developer must pre-package the necessary Meltano plugins as standalone Python wheel (.whl) files or localized Git repositories. These are then installed locally using the meltano add \--custom command, specifying a local pip\_url that points directly to the validated, permissive-licensed artifact residing safely within the secure perimeter.21 This offline installation workflow guarantees that the supply chain remains immutable and impervious to external network poisoning.26

### **Profiling, Compute, & Resource Exhaustion (DuckDB)**

DuckDB serves as the analytical engine tasked with deriving the statistical shapes, bounds, and null-rates from the raw PII. Licensed under the MIT license, it is inherently compliant with the platform's constraints.3 Because DuckDB runs in-process within the Python application, it avoids the network vulnerability surface typical of traditional client-server databases like PostgreSQL; there are no network ports to scan and no persistent daemon credentials to compromise.

However, processing massive enterprise datasets natively in-memory introduces a profound risk of resource exhaustion and Denial of Service (DoS), an established OWASP concern.49 If a Singer tap extracts a billion-row table into DuckDB without constraint, the host machine will inevitably suffer an Out-of-Memory (OOM) crash, halting the generation pipeline. To defend against memory exhaustion, the architecture enforces strict programmatic constraints on the DuckDB connection. Developers must initialize the DuckDB instance with defensive pragmas, explicitly limiting thread counts and maximum memory consumption using SQL statements such as SET memory\_limit \= '16GB'; and SET threads \= 4;.4

If the volume of the source data exceeds the available physical memory allocated by the limit, DuckDB will gracefully attempt to spill intermediate results to disk.4 To prevent sensitive PII from being written to persistent, recoverable storage during this spillover event, the temporary directory must be mapped exclusively to a volatile RAM disk using the command SET temp\_directory \= '/tmpfs/duckdb\_spill';.4

Furthermore, DuckDB executes SQL queries with the full privileges of the host process. To prevent SQL injection (OWASP A03:2021) originating from maliciously crafted source data or corrupted user configurations, the architecture absolutely forbids dynamic string concatenation in query construction.49 All analytical and aggregation queries must be executed using strict parameterized statements native to the DuckDB Python client, thoroughly neutralizing the threat of arbitrary SQL execution.51

### **Structured Generation & Cryptographic Erasure (Python & Faker)**

The Python 3.11/3.12 runtime powers the structured synthesis phase. While generating synthetic data via the Faker library and the SciPy/NumPy stack presents no direct licensing issues (all rely on permissive licenses), the handling of the initial configuration files and the eradication of the original memory buffers pose significant security challenges that require meticulous architectural design.8

The system relies heavily on YAML configuration files to define the desired schemas, masking rules, relationship constraints, and distribution parameters for the synthetic output. A critical vulnerability exists within the native Python ecosystem regarding YAML: invoking the standard yaml.load() function allows for arbitrary remote code execution during the deserialization of untrusted YAML documents.55 To counter this OWASP A08:2021 (Software and Data Integrity Failures) vulnerability, the architecture mandates the use of the Pydantic library for all configuration ingestion. Pydantic strictly validates incoming configurations against strongly typed Python BaseModel classes, automatically rejecting malformed structures, enforcing data types, and inherently utilizing safe parsing mechanics.56 This ensures that maliciously altered configuration files cannot trigger a deserialization exploit.

Furthermore, Python's underlying memory management introduces profound data remanence vulnerabilities. Python string objects are immutable; once a string containing a social security number, a credit card track, or a medical diagnosis is created in memory, it cannot be modified in place.29 When the variable reference is deleted by the developer, the Python garbage collector will eventually reclaim the memory, but the plaintext bytes remain physically present in RAM until they are arbitrarily overwritten by the operating system. This leaves the system vulnerable to memory-dump attacks or cold-boot exploits.6

To enforce true cryptographic erasure of the profiling data before the synthesis phase begins, the architecture requires that sensitive data streams be ingested into mutable bytearray objects rather than native strings.6 Once the statistical profiling is complete and the metadata is safely extracted, these bytearray objects must be explicitly overwritten with cryptographically secure random bytes generated by the Python secrets module.30 Relying on the standard random module is strictly prohibited, as its deterministic pseudo-random nature is insufficient for secure data obfuscation.59 Only after the memory has been securely overwritten is the gc.collect() garbage collection routine invoked.

### **Unstructured Generation & Inference Isolation (Ollama)**

The generation of realistic unstructured text—such as mock clinical encounter notes, customer chat transcripts, or unstructured JSON blobs—is handled by Ollama, which is licensed under the MIT license.60 Ollama enables the local execution of highly capable, quantized Large Language Models without the unacceptable latency or exfiltration risk associated with external API calls to commercial providers like OpenAI or Anthropic.10

However, local LLM execution presents unique threat vectors. First, the models (e.g., Llama 3, Mistral) must be pre-downloaded, audited, and transported across the air-gap on secure media, as the air-gapped Ollama instance cannot phone home to fetch weights dynamically.11 Second, Ollama ships with default behaviors that could compromise an air-gapped security posture, including automatic update polling and usage telemetry.12 The architecture mandates that Ollama be instantiated with strict environment variable overrides, notably setting OLLAMA\_TELEMETRY=0 and disabling automatic updates to block all outgoing network attempts entirely.12

Additionally, prompt injection attacks (a primary concern in the OWASP Top 10 for LLMs) remain a localized threat. If the statistical metadata contains malformed string distributions, it could manipulate the local LLM into entering an infinite generation loop, causing a catastrophic denial of service. The Python orchestration layer must enforce rigid prompt templates, effectively sandboxing the variables injected into the context window.32 The architecture also dictates the use of the num\_ctx parameter to place an absolute upper bound on memory consumption during unstructured inference, preventing run-away token generation.33 Furthermore, model weights must be restricted entirely to GPU memory wherever possible to prevent sensitive intermediate attention states from swapping into system RAM, a state achieved by monitoring the execution matrix and ensuring 100% GPU allocation.33

### **Transformation & Statistical Validation (dbt)**

Following the synthesis of both structured tabular data and unstructured narrative text, the raw synthetic results are loaded into a new, entirely isolated DuckDB instance. dbt (licensed under Apache 2.0) and the dbt-expectations extension (licensed under MIT) serve as the final validation gateway.14 The purpose of this layer is not merely to perform standard data transformations, but to cryptographically and statistically verify that the synthetic output mathematically mirrors the original metadata profile.

Because the original dbt-expectations package is no longer actively maintained, the architecture specifically mandates the use of the maintained fork provided by Metaplane to ensure compatibility with modern dbt versions and to mitigate the risk of utilizing abandoned software.14 Using this package, the developer defines specific tolerance thresholds within the dbt\_project.yml and associated schema files. For example, expectations such as expect\_column\_mean\_to\_be\_between, expect\_column\_distinct\_values\_to\_equal\_set, or expect\_table\_row\_count\_to\_equal ensure that the synthetic datasets preserve the referential integrity, covariance, and statistical reality required for downstream AI training and business intelligence validation.15

Security within the dbt layer requires strict adherence to secure templating practices. Developers must avoid injecting raw user input into dbt Jinja macros to prevent secondary SQL injection attacks during the compilation phase.63 All configurations passed to the validation models must be structurally validated. If a dataset fails the dbt-expectations assertions, the Dagster orchestrator captures the failure, flags the asset as invalid in the lineage graph, and halts the deployment of the synthetic data to the target sink, ensuring that flawed data never reaches the consumer.

## **Threat Model & Security Matrix**

The architecture assumes a highly adversarial environment where internal actors, compromised supply-chain dependencies, or inadvertent misconfigurations attempt to exfiltrate data, execute arbitrary code, or recover purged memory. The threat model systematically maps specific attack vectors to architectural countermeasures, prioritizing the mitigation of vulnerabilities outlined in the OWASP Top 10 framework.

### **The Data Remanence Paradigm**

The most unique and insidious threat to this platform is data remanence. Typical data engineering pipelines treat memory and temporary files as transient, assuming that once a job completes, the data is functionally gone. However, when handling PHI and PII, volatile memory and swap space must be treated as highly sensitive until demonstrably and cryptographically purged.

The architecture relies heavily on the operating system-level tmpfs construct. By mounting the DuckDB temp directory and the Meltano internal state directories to a tmpfs volume, the data physically resides in RAM, never touching the underlying persistent solid-state drive (SSD) or hard disk drive (HDD).31 However, because RAM can be captured during a cold-boot attack or intercepted by compromised sibling processes, simply unlinking the files via standard OS commands (e.g., os.remove()) is insufficient, as it merely removes the file system pointer while leaving the data intact.7 To resolve this, the architecture executes the Linux shred utility against the tmpfs files.7 Specifically, a command such as shred \-vuz \-n 3 is executed to explicitly overwrite the memory addresses with random patterns for three passes, followed by a final zero-pass, before unlinking the file and releasing the allocation back to the kernel.7

### **Component-Level Threat Matrix**

The following table details the primary attack vectors, their OWASP classification, and the strict architectural mitigations enforced by the platform.

| Threat Vector | OWASP Classification | Component Focus | Architectural Mitigation Strategy |
| :---- | :---- | :---- | :---- |
| **YAML Deserialization RCE** | A08:2021 \- Software & Data Integrity Failures | Python Config Parser | Prohibit the use of yaml.load(). Mandate Pydantic BaseModel parsing and validation for all configurations, ensuring strict structural typing and the immediate rejection of executable payloads.55 |
| **SQL Injection (SQLi)** | A03:2021 \- Injection | DuckDB / dbt | Ban dynamic string concatenation in DuckDB Python clients. Enforce strict parameterization for all analytical queries. Utilize SQLFluff linting to enforce secure Jinja templating in dbt macros.50 |
| **In-Memory Data Remanence** | A04:2021 \- Insecure Design | Python Runtime | PII must not be stored in immutable string objects. Utilize mutable bytearray structures and overwrite buffers using the cryptographically secure secrets module before triggering Python garbage collection.6 |
| **Disk Spill PII Recovery** | A04:2021 \- Insecure Design | DuckDB Storage | Mount DuckDB temporary directories exclusively to a tmpfs RAM disk. Execute the Linux shred command with multi-pass zero-filling upon pipeline completion to prevent forensic recovery.4 |
| **Copyleft License Violation** | Legal / Compliance Risk | Singer Taps | Forbid AGPL-licensed PipelineWise connectors. Audit and enforce the exclusive use of Apache 2.0 / MIT MeltanoLabs variants via offline .whl local deployments.21 |
| **LLM Telemetry Exfiltration** | A01:2021 \- Broken Access Control | Ollama Daemon | Enforce absolute network isolation (air-gap). Inject environment variables (e.g., OLLAMA\_TELEMETRY=0) to suppress outbound calls. Restrict model inference to localized GPU boundaries.12 |
| **Denial of Service (OOM)** | A05:2021 \- Security Misconfiguration | DuckDB / Ollama | Hardcode defensive limits: enforce DuckDB memory\_limit and threads pragmas. Constrain Ollama num\_ctx window sizes to prevent infinite recursive generation loops.4 |
| **Supply Chain Poisoning** | A06:2021 \- Vulnerable Components | Dagster / Pip Packages | Mandate offline installation from vetted internal artifacts using pip-audit. Isolate the Dagster webserver execution from user-code execution, preventing poisoned pipelines from compromising the control plane.37 |

## **Dependency & License Audit**

A cornerstone of this architecture is the uncompromising adherence to permissive licensing. The integration of copyleft licenses (such as GPL or AGPL) creates an unacceptable risk of intellectual property exposure, as they mandate the release of the encompassing application's source code if distributed over a network.24 To ensure continuous compliance, the platform mandates the use of automated license auditing tools, such as Qodana or pip-audit, to parse dependency trees and flag incompatible software licenses before they cross the air-gap.67

The following audit defines the core components of the platform, their explicitly verified licenses, and the justification for their inclusion in a stable, enterprise-grade environment.

| Component Category | Technology Choice | Explicit License | Copyleft Risk Profile | Enterprise Stability Justification |
| :---- | :---- | :---- | :---- | :---- |
| **Ingestion Engine** | Meltano | MIT | **None** | Highly mature, text-based ELT framework supporting modular Singer specifications and offline installation mechanisms.1 |
| **Database Connectors** | MeltanoLabs Singer Taps | Apache 2.0 / MIT | **SEVERE** | *Warning:* Standard PipelineWise taps are AGPL-3.0.44 MeltanoLabs variants are explicitly vetted and selected to avoid copyleft contamination.23 |
| **Analytical Compute** | DuckDB | MIT | **None** | Highly performant in-process execution removes network latency and database credential management vulnerabilities.3 |
| **Data Generation** | Python 3.11 / Faker | Python / MIT | **None** | Industry standard for statistical modeling and data mocking. Heavily documented and supported by a vast, stable ecosystem.8 |
| **AI Inference** | Ollama | MIT | **None** | Allows localized, API-free LLM execution. Source available and highly optimized for local hardware constraints.10 |
| **Transformation** | dbt-core | Apache 2.0 | **None** | Unrivaled standard in analytics engineering; provides deterministic, version-controlled data transformations.22 |
| **Validation** | dbt-expectations | MIT / Apache | **None** | Forked and maintained by Metaplane; provides critical statistical assertions directly within the dbt DAG without introducing heavy external dependencies.14 |
| **Orchestration** | Dagster (OSS) | Apache 2.0 | **None** | Software-Defined Assets provide unparalleled observability into data lineage, natively supporting local development via dg dev.39 |

## **Atomic Module Breakdown**

To accommodate a highly autonomous, single-developer lifecycle, the system architecture is deliberately decomposed into six strictly isolated modules. Each module features rigidly defined input-output contracts, allowing the engineer to develop, test, and tear down components in single-session increments without the cognitive burden or overhead of instantiating the entire pipeline. This decoupling relies heavily on the Dagster asset graph to manage state transitions between the modules.

### **Module 1: Secure Infrastructure & Orchestration Bootstrapping**

* **Objective**: Establish the isolated execution environment, configure the localized storage, and initialize the Dagster control plane.  
* **Implementation**: The developer configures the local Python virtual environments and provisions the tmpfs RAM disk for ephemeral storage. Dagster is initialized using the dg dev command, establishing the dagster.yaml configurations and defining the isolated code locations for the webserver and user-code environments.37  
* **Input**: Local configuration files and environment variables (e.g., DAGSTER\_HOME).  
* **Output**: A running, secure Dagster daemon ready to accept asset definitions, with strict process isolation verified.

### **Module 2: Air-Gapped Ingestion & Profiling Engine**

* **Objective**: Extract raw PII from the source database and generate the safe, statistical metadata profile.  
* **Implementation**: Meltano invokes the pre-compiled, Apache-licensed MeltanoLabs Singer taps from local wheel files.21 Data streams directly into the in-memory DuckDB instance.27 DuckDB executes parameterized aggregation queries to compute schemas, standard deviations, and distributions, utilizing the tmpfs volume if disk spilling is required.4  
* **Input**: Raw production database records and table schemas.  
* **Output**: A sanitized JSON/YAML metadata profile completely devoid of actual source values, representing the mathematical shape of the data.

### **Module 3: Cryptographic Teardown & Erasure**

* **Objective**: Guarantee the total destruction of all raw data artifacts and intermediate memory buffers before generation begins.  
* **Implementation**: This module operates as an automated procedural hook triggered by Dagster immediately following the successful generation of the metadata profile. It executes bytearray overwriting in Python using the secrets module and invokes the Linux shred utility against the tmpfs spillover directories to destroy any lingering artifacts.6  
* **Input**: Termination signal from the profiling phase.  
* **Output**: A cryptographically sanitized memory state, verified by the absence of source data structures.

### **Module 4: Structured Tabular Synthesis**

* **Objective**: Generate structured tabular data that adheres perfectly to the derived statistical rules.  
* **Implementation**: Utilizing Python 3.11/3.12, Faker, and SciPy, the module parses the metadata profile via Pydantic.56 It utilizes inverse transform sampling and covariance matrices to generate discrete and continuous variables that perfectly mirror the original distributions.  
* **Input**: The sanitized metadata profile output from Module 2\.  
* **Output**: Heavily populated, structurally identical synthetic DuckDB tables residing entirely in memory.

### **Module 5: Unstructured Semantic Generation**

* **Objective**: Populate free-text fields with statistically relevant, highly realistic mock narratives.  
* **Implementation**: Using the isolated Ollama daemon with telemetry explicitly disabled (OLLAMA\_TELEMETRY=0), the module injects sanitized context bounds into predefined prompt templates.12 The quantized local LLM generates contextual medical or financial notes that align temporally and categorically with the generated structured variables.10  
* **Input**: Contextual prompt parameters derived from the generated structured data.  
* **Output**: Synthetic unstructured text blocks appended to the appropriate columns within the DuckDB sink.

### **Module 6: Statistical Validation & Materialization**

* **Objective**: Cryptographically and statistically verify the fidelity of the synthetic output before final export.  
* **Implementation**: Dagster triggers the dbt build command via the dagster-dbt integration.35 dbt-expectations executes a comprehensive suite of assertions comparing the variance, row counts, and referential integrity of the synthetic data against the strict tolerances defined in the source metadata.16  
* **Input**: The fully populated synthetic DuckDB tables.  
* **Output**: Finalized, validated synthetic assets materialized into the target local BYOC destination, accompanied by a deterministic pass/fail audit log visible within the Dagster UI.

#### **Works cited**

1. meltano \- PyPI, accessed March 1, 2026, [https://pypi.org/project/meltano/](https://pypi.org/project/meltano/)  
2. meltano/LICENSE at main \- GitHub, accessed March 1, 2026, [https://github.com/meltano/meltano/blob/main/LICENSE](https://github.com/meltano/meltano/blob/main/LICENSE)  
3. The Enterprise Case for DuckDB: 5 Key Categories and Why Use It \- MotherDuck, accessed March 1, 2026, [https://motherduck.com/blog/duckdb-enterprise-5-key-categories/](https://motherduck.com/blog/duckdb-enterprise-5-key-categories/)  
4. Memory Management in DuckDB, accessed March 1, 2026, [https://duckdb.org/2024/07/09/memory-management](https://duckdb.org/2024/07/09/memory-management)  
5. A Developer's Guide to Handling Sensitive Data With DuckDB \- DZone, accessed March 1, 2026, [https://dzone.com/articles/developers-guide-handling-sensitive-data-with-duckdb](https://dzone.com/articles/developers-guide-handling-sensitive-data-with-duckdb)  
6. Overwriting memory in Python \- Sjoerd Langkemper, accessed March 1, 2026, [https://www.sjoerdlangkemper.nl/2016/06/09/clearing-memory-in-python/](https://www.sjoerdlangkemper.nl/2016/06/09/clearing-memory-in-python/)  
7. How to Securely Erase a Disk and File using the Linux shred Command, accessed March 1, 2026, [https://www.freecodecamp.org/news/securely-erasing-a-disk-and-file-using-linux-command-shred/](https://www.freecodecamp.org/news/securely-erasing-a-disk-and-file-using-linux-command-shred/)  
8. Faker vulnerabilities \- Snyk Security Database, accessed March 1, 2026, [https://security.snyk.io/package/pip/Faker](https://security.snyk.io/package/pip/Faker)  
9. Python Tip \#1: Faker library \- by Hajar Zankadi \- Medium, accessed March 1, 2026, [https://medium.com/@hajar.zankadi/python-tips-1-faker-library-5d1f3b3eeb62](https://medium.com/@hajar.zankadi/python-tips-1-faker-library-5d1f3b3eeb62)  
10. Deploy LLMs Locally with Ollama: Your Complete Guide to Local AI Development \- Medium, accessed March 1, 2026, [https://medium.com/@bluudit/deploy-llms-locally-with-ollama-your-complete-guide-to-local-ai-development-ba60d61b6cea](https://medium.com/@bluudit/deploy-llms-locally-with-ollama-your-complete-guide-to-local-ai-development-ba60d61b6cea)  
11. Running Ollama fully air-gapped, anyone else? \- Reddit, accessed March 1, 2026, [https://www.reddit.com/r/ollama/comments/1qsjn38/running\_ollama\_fully\_airgapped\_anyone\_else/](https://www.reddit.com/r/ollama/comments/1qsjn38/running_ollama_fully_airgapped_anyone_else/)  
12. Disable Telemetry \#5554 \- anomalyco/opencode \- GitHub, accessed March 1, 2026, [https://github.com/anomalyco/opencode/issues/5554](https://github.com/anomalyco/opencode/issues/5554)  
13. Best Practices for Securing LLM-Enabled Applications | NVIDIA Technical Blog, accessed March 1, 2026, [https://developer.nvidia.com/blog/best-practices-for-securing-llm-enabled-applications/](https://developer.nvidia.com/blog/best-practices-for-securing-llm-enabled-applications/)  
14. dbt\_expectations \- dbt \- Package hub, accessed March 1, 2026, [https://hub.getdbt.com/metaplane/dbt\_expectations/latest](https://hub.getdbt.com/metaplane/dbt_expectations/latest)  
15. dbt with expectations\!. Hi | by Michaël Scherding \- Medium, accessed March 1, 2026, [https://michael-scherding.medium.com/dbt-with-expectations-d6a487158385](https://michael-scherding.medium.com/dbt-with-expectations-d6a487158385)  
16. dbt-expectations: What it is and how to use it to find data quality issues | Metaplane, accessed March 1, 2026, [https://www.metaplane.dev/blog/dbt-expectations](https://www.metaplane.dev/blog/dbt-expectations)  
17. Dagster vs Prefect: Compare Modern Orchestration Tools, accessed March 1, 2026, [https://dagster.io/vs/dagster-vs-prefect](https://dagster.io/vs/dagster-vs-prefect)  
18. Projects | Meltano Documentation, accessed March 1, 2026, [https://docs.meltano.com/concepts/project\#offline-mode](https://docs.meltano.com/concepts/project#offline-mode)  
19. Installing Packer Plugins in an Air-Gap environment : r/hashicorp \- Reddit, accessed March 1, 2026, [https://www.reddit.com/r/hashicorp/comments/150juzw/installing\_packer\_plugins\_in\_an\_airgap\_environment/](https://www.reddit.com/r/hashicorp/comments/150juzw/installing_packer_plugins_in_an_airgap_environment/)  
20. Air-Gapped Deployment for Community Edition: Best Practices and Workflow \- hoop.dev, accessed March 1, 2026, [https://hoop.dev/blog/air-gapped-deployment-for-community-edition-best-practices-and-workflow-2/](https://hoop.dev/blog/air-gapped-deployment-for-community-edition-best-practices-and-workflow-2/)  
21. General Usage | Meltano Documentation, accessed March 1, 2026, [https://docs.meltano.com/guide/plugin-management/](https://docs.meltano.com/guide/plugin-management/)  
22. dbt Licensing FAQ | Understand licensing around dbt Core, dbt Fusion engine & More, accessed March 1, 2026, [https://www.getdbt.com/licenses-faq](https://www.getdbt.com/licenses-faq)  
23. Software Licenses \- TAP-OS | Open Source Attribution, accessed March 1, 2026, [https://www.tap-os.app/licenses](https://www.tap-os.app/licenses)  
24. This Is Why You Always Review Your Dependencies, AGPL Edition \- Andrew Ayer, accessed March 1, 2026, [https://www.agwa.name/blog/post/always\_review\_your\_dependencies](https://www.agwa.name/blog/post/always_review_your_dependencies)  
25. What are Apache, GPL and AGPL licenses – misconceptions, lies, facts \- Reddit, accessed March 1, 2026, [https://www.reddit.com/r/opensource/comments/197gw70/what\_are\_apache\_gpl\_and\_agpl\_licenses/](https://www.reddit.com/r/opensource/comments/197gw70/what_are_apache_gpl_and_agpl_licenses/)  
26. Complete ELT Walkthrough | Meltano Documentation, accessed March 1, 2026, [https://docs.meltano.com/getting-started/](https://docs.meltano.com/getting-started/)  
27. Python API \- DuckDB, accessed March 1, 2026, [https://duckdb.org/docs/stable/clients/python/overview](https://duckdb.org/docs/stable/clients/python/overview)  
28. Data-at-Rest Encryption in DuckDB, accessed March 1, 2026, [https://duckdb.org/2025/11/19/encryption-in-duckdb](https://duckdb.org/2025/11/19/encryption-in-duckdb)  
29. Securely Erasing Password in Memory (Python) \- Stack Overflow, accessed March 1, 2026, [https://stackoverflow.com/questions/728164/securely-erasing-password-in-memory-python](https://stackoverflow.com/questions/728164/securely-erasing-password-in-memory-python)  
30. Using a bytearray rather than a string to store password in memory, accessed March 1, 2026, [https://softwareengineering.stackexchange.com/questions/270113/using-a-bytearray-rather-than-a-string-to-store-password-in-memory](https://softwareengineering.stackexchange.com/questions/270113/using-a-bytearray-rather-than-a-string-to-store-password-in-memory)  
31. Securely wiping a file on a tmpfs \- linux \- Super User, accessed March 1, 2026, [https://superuser.com/questions/668029/securely-wiping-a-file-on-a-tmpfs](https://superuser.com/questions/668029/securely-wiping-a-file-on-a-tmpfs)  
32. The LLM Security Checklist: How to Prevent Data Leaks from Your Private Database | by Pratish Dewangan | Medium, accessed March 1, 2026, [https://medium.com/@dpratishraj7991/the-llm-security-checklist-how-to-prevent-data-leaks-from-your-private-database-6501bba65dcb](https://medium.com/@dpratishraj7991/the-llm-security-checklist-how-to-prevent-data-leaks-from-your-private-database-6501bba65dcb)  
33. FAQ \- Ollama, accessed March 1, 2026, [https://docs.ollama.com/faq](https://docs.ollama.com/faq)  
34. Dagster vs Prefect: Comparing Features, Use Cases, and Workflow Orchestration | Decube, accessed March 1, 2026, [https://www.decube.io/post/dagster-prefect-compare](https://www.decube.io/post/dagster-prefect-compare)  
35. Orchestrate your dbt™ transformation steps \- Dagster, accessed March 1, 2026, [https://dagster.io/integrations/dagster-dbt](https://dagster.io/integrations/dagster-dbt)  
36. How to Orchestrate dbt with Dagster, accessed March 1, 2026, [https://dagster.io/blog/orchestrating-dbt-with-dagster](https://dagster.io/blog/orchestrating-dbt-with-dagster)  
37. Dagster's Code Location Architecture, accessed March 1, 2026, [https://dagster.io/blog/dagster-code-locations](https://dagster.io/blog/dagster-code-locations)  
38. Running Dagster locally, accessed March 1, 2026, [https://docs.dagster.io/deployment/oss/deployment-options/running-dagster-locally](https://docs.dagster.io/deployment/oss/deployment-options/running-dagster-locally)  
39. Deployment overview \- Dagster Docs, accessed March 1, 2026, [https://docs.dagster.io/deployment](https://docs.dagster.io/deployment)  
40. Deployment options | Dagster Docs, accessed March 1, 2026, [https://docs.dagster.io/deployment/oss/deployment-options](https://docs.dagster.io/deployment/oss/deployment-options)  
41. Orchestration: Thoughts on Dagster, Airflow and Prefect? : r/dataengineering \- Reddit, accessed March 1, 2026, [https://www.reddit.com/r/dataengineering/comments/13xkeov/orchestration\_thoughts\_on\_dagster\_airflow\_and/](https://www.reddit.com/r/dataengineering/comments/13xkeov/orchestration_thoughts_on_dagster_airflow_and/)  
42. Orchestration Showdown: Dagster vs Prefect vs Airflow \- ZenML Blog, accessed March 1, 2026, [https://www.zenml.io/blog/orchestration-showdown-dagster-vs-prefect-vs-airflow](https://www.zenml.io/blog/orchestration-showdown-dagster-vs-prefect-vs-airflow)  
43. Singer.io Tap for MySQL \- GitHub, accessed March 1, 2026, [https://github.com/singer-io/tap-mysql](https://github.com/singer-io/tap-mysql)  
44. Licenses — PipelineWise documentation \- GitHub Pages, accessed March 1, 2026, [https://transferwise.github.io/pipelinewise/project/licenses.html](https://transferwise.github.io/pipelinewise/project/licenses.html)  
45. singer-io/tap-postgres \- GitHub, accessed March 1, 2026, [https://github.com/singer-io/tap-postgres](https://github.com/singer-io/tap-postgres)  
46. MeltanoLabs/tap-postgres: Singer Tap for PostgreSQL \- GitHub, accessed March 1, 2026, [https://github.com/MeltanoLabs/tap-postgres](https://github.com/MeltanoLabs/tap-postgres)  
47. MeltanoLabs/tap-mysql: Singer compliant tap for mysql \- GitHub, accessed March 1, 2026, [https://github.com/MeltanoLabs/tap-mysql](https://github.com/MeltanoLabs/tap-mysql)  
48. Command Line | Meltano Documentation, accessed March 1, 2026, [https://docs.meltano.com/reference/command-line-interface/](https://docs.meltano.com/reference/command-line-interface/)  
49. Securing DuckDB, accessed March 1, 2026, [https://duckdb.org/docs/stable/operations\_manual/securing\_duckdb/overview](https://duckdb.org/docs/stable/operations_manual/securing_duckdb/overview)  
50. A05 Injection \- OWASP Top 10:2025, accessed March 1, 2026, [https://owasp.org/Top10/2025/A05\_2025-Injection/](https://owasp.org/Top10/2025/A05_2025-Injection/)  
51. SQL Injection Prevention \- OWASP Cheat Sheet Series, accessed March 1, 2026, [https://cheatsheetseries.owasp.org/cheatsheets/SQL\_Injection\_Prevention\_Cheat\_Sheet.html](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html)  
52. Preventing SQL Injection Attacks With Python, accessed March 1, 2026, [https://realpython.com/prevent-python-sql-injection/](https://realpython.com/prevent-python-sql-injection/)  
53. faker-file \- PyPI, accessed March 1, 2026, [https://pypi.org/project/faker-file/](https://pypi.org/project/faker-file/)  
54. joke2k/faker: Faker is a Python package that generates fake data for you. \- GitHub, accessed March 1, 2026, [https://github.com/joke2k/faker](https://github.com/joke2k/faker)  
55. Deserialization \- OWASP Cheat Sheet Series, accessed March 1, 2026, [https://cheatsheetseries.owasp.org/cheatsheets/Deserialization\_Cheat\_Sheet.html](https://cheatsheetseries.owasp.org/cheatsheets/Deserialization_Cheat_Sheet.html)  
56. How to Validate Config YAML with Pydantic in Machine Learning Pipelines, accessed March 1, 2026, [https://www.sarahglasmacher.com/how-to-validate-config-yaml-pydantic/](https://www.sarahglasmacher.com/how-to-validate-config-yaml-pydantic/)  
57. Keeping Configurations Sane with Pydantic Settings \- AI Logs, accessed March 1, 2026, [https://ai.ragv.in/posts/sane-configs-with-pydantic-settings/](https://ai.ragv.in/posts/sane-configs-with-pydantic-settings/)  
58. secrets — Generate secure random numbers for managing secrets — Python 3.14.3 documentation, accessed March 1, 2026, [https://docs.python.org/3/library/secrets.html](https://docs.python.org/3/library/secrets.html)  
59. Secure File Deletion using Python | by Ramanan Subramanian \- Medium, accessed March 1, 2026, [https://ramanantechpro.medium.com/secure-file-deletion-using-python-d0456a0cfc98](https://ramanantechpro.medium.com/secure-file-deletion-using-python-d0456a0cfc98)  
60. ollama/ollama: Get up and running with Kimi-K2.5, GLM-5, MiniMax, DeepSeek, gpt-oss, Qwen, Gemma and other models. \- GitHub, accessed March 1, 2026, [https://github.com/ollama/ollama](https://github.com/ollama/ollama)  
61. Setting up an airgapped LLM using Ollama \- DEV Community, accessed March 1, 2026, [https://dev.to/florianlutz/setting-up-an-airgapped-llm-using-ollama-2il4](https://dev.to/florianlutz/setting-up-an-airgapped-llm-using-ollama-2il4)  
62. turning off crew ai telemetry · Issue \#241 · crewAIInc/crewAI \- GitHub, accessed March 1, 2026, [https://github.com/crewAIInc/crewAI/issues/241](https://github.com/crewAIInc/crewAI/issues/241)  
63. sql\_header | dbt Developer Hub, accessed March 1, 2026, [https://docs.getdbt.com/reference/resource-configs/sql\_header](https://docs.getdbt.com/reference/resource-configs/sql_header)  
64. Deleting Files in Python: Step-by-Step Instructions and Best Practices \- Skills Data Analytics, accessed March 1, 2026, [https://skills-datanalytics.com/blogs/deleting-files-in-python-step-by-step-instructions-and-best-practices](https://skills-datanalytics.com/blogs/deleting-files-in-python-step-by-step-instructions-and-best-practices)  
65. Securely wipe disk/Tips and tricks \- ArchWiki, accessed March 1, 2026, [https://wiki.archlinux.org/title/Securely\_wipe\_disk/Tips\_and\_tricks](https://wiki.archlinux.org/title/Securely_wipe_disk/Tips_and_tricks)  
66. Lint and format your code | dbt Developer Hub, accessed March 1, 2026, [https://docs.getdbt.com/docs/cloud/studio-ide/lint-format](https://docs.getdbt.com/docs/cloud/studio-ide/lint-format)  
67. How to Secure FastAPI Applications Against OWASP Top 10 \- OneUptime, accessed March 1, 2026, [https://oneuptime.com/blog/post/2025-01-06-fastapi-owasp-security/view](https://oneuptime.com/blog/post/2025-01-06-fastapi-owasp-security/view)  
68. License audit | Qodana Documentation \- JetBrains, accessed March 1, 2026, [https://www.jetbrains.com/help/qodana/license-audit.html](https://www.jetbrains.com/help/qodana/license-audit.html)  
69. dbt Pricing Plans — flexible options for every team | dbt Labs, accessed March 1, 2026, [https://www.getdbt.com/pricing](https://www.getdbt.com/pricing)