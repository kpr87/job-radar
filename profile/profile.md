# Candidate profile: Jordan Example

<!--
This is the document Claude ranks every posting against. It ships as a worked EXAMPLE for a fictional
senior data engineer. Replace all of it with your own facts and keep the same headings.

Rules that make the ranking honest:
- Write only what is true and what you could defend in an interview. Claude is told not to assume
  experience the profile does not show.
- Be specific: named systems, scale, your role. "Built the nightly Spark pipeline for 40 million rows"
  ranks better than "experienced with big data".
- Say what you do NOT have (the "Hard gaps" section). That is what stops poor matches reaching your inbox.
- Keep your copy of this repository PRIVATE. This file is personal.
-->

## Target roles
- Primary: Senior Data Engineer, Staff Data Engineer, Data Platform Engineer, Analytics Engineer.
- Also: Lead Data Engineer with a small team, Machine Learning Engineer roles that are mostly data pipelines.
- Level: senior individual contributor or lead. Not junior, not pure people management.
- Employment: full-time roles preferred; contract roles are fine if they are six months or longer.
- Location: remote anywhere in the US, or hybrid or onsite within 30 miles of Chicago.

## Positioning, in order of strength
1. Batch and streaming data pipelines on Spark and Airflow, feeding a Snowflake warehouse.
2. Data modeling and transformation with dbt; data quality checks and lineage.
3. Cloud infrastructure for data on AWS (S3, EMR, Glue, Lambda) with Terraform.
- Kafka experience is real but narrow: one clickstream ingestion pipeline. Not a Kafka platform specialist.

## Experience summary (9 years)
- Lakeshore Retail (fictional, 2021 to present), Senior Data Engineer.
  - Rebuilt nightly batch ETL as incremental Spark jobs on EMR orchestrated by Airflow; load time down from 6 hours to 50 minutes.
  - Owns the dbt project behind finance and merchandising reporting (about 400 models) and its data quality tests.
  - Clickstream ingestion from Kafka into S3 and Snowflake, about 200 million events a day.
  - Mentors two engineers; on-call for the data platform.
- Prairie Analytics (fictional, 2017 to 2021), Data Engineer.
  - Built customer and order pipelines in Python and SQL on Redshift; migrated them to Snowflake.
  - Terraform modules for the team's AWS data infrastructure.
- Earlier (2015 to 2017): BI developer, SQL Server and SSIS.

## Confirmed skills
Data: Spark, Airflow, dbt, Snowflake, Redshift, Kafka (ingestion), data modeling, ETL and ELT, data quality, lineage.
Cloud: AWS (S3, EMR, Glue, Lambda, IAM), Terraform, Docker, CI/CD with GitHub Actions.
Languages: Python, SQL, some Scala.
Reporting: Looker, Tableau.

## Not confirmed (do not count toward fit; list as a gap when a posting requires them)
Databricks, BigQuery and the rest of Google Cloud, Flink, Kubernetes in production.

## Credentials
- B.S. in Computer Science.
- AWS Certified Data Engineer, Associate.
- No Snowflake or Databricks certification.

## Hard gaps (treat as real gaps when the posting REQUIRES them)
- An active security clearance or US federal government work.
- A platform never used as the main tool: Informatica, Teradata, Ab Initio, Azure-only stacks.
- Machine learning research or model training as the core of the job.
- Healthcare data standards (HL7, FHIR) or a required healthcare background.
- Required certifications not held, when listed as mandatory.

## Calibration (optional: your own judgment of past postings, to anchor the scores)
- A staff data engineer role on a Spark and Snowflake stack at a retailer: about 85% fit.
- A data engineer role that is mostly Databricks and Azure: about 60% fit. Main gap: the platform.
- A machine learning engineer role centered on model training: about 35% fit.
