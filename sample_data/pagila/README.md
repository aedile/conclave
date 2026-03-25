# Pagila Sample Dataset

## Source

Official Pagila repository: https://github.com/devrimgunduz/pagila

Pagila is the PostgreSQL port of the MySQL Sakila sample database, maintained by
Devrim Gunduz. It models a DVD rental store with realistic relational data across
15 tables including customers, films, rentals, inventory, staff, and payments.

## License

PostgreSQL License — https://opensource.org/licenses/postgresql

The Pagila dataset is distributed under the PostgreSQL License, a liberal
open-source license similar to the BSD 2-Clause License. You are free to use,
modify, and distribute it for any purpose.

## Provisioning

Run the provisioning script to download and load Pagila into a local PostgreSQL
instance:

```bash
export PGHOST=localhost
export PGPORT=5432
export PGUSER=postgres
export PGPASSWORD=your_password

bash scripts/provision_pagila.sh
```

The script is idempotent — running it multiple times always produces a clean
`pagila` database. It requires PostgreSQL >= 16.

### Prerequisites

- PostgreSQL >= 16 server and `psql` client
- `curl`
- `sha256sum` (Linux) or `shasum` (macOS)

### What the script does

1. Checks that PostgreSQL >= 16 is reachable
2. Downloads `pagila-schema.sql` and `pagila-data.sql` from the official repo over HTTPS
3. Verifies SHA-256 checksums of both files (pinned in the script)
4. Drops and recreates the `pagila` database (DROP DATABASE IF EXISTS)
5. Loads the schema, then the data
6. Validates post-load row counts (customer >= 500, rental >= 40000)
7. Validates that all foreign key constraints are satisfied

## Table List with Approximate Row Counts

| Table          | Approx. Rows | Description                              |
|----------------|--------------|------------------------------------------|
| actor          | 200          | Actors appearing in films                |
| address        | 603          | Addresses for customers and stores       |
| category       | 16           | Film genre categories                    |
| city           | 600          | Cities referenced by addresses           |
| country        | 109          | Countries referenced by cities           |
| customer       | 599          | Store customers                          |
| film           | 1000         | Film catalogue                           |
| film_actor     | 5462         | Many-to-many: films to actors            |
| film_category  | 1000         | Many-to-many: films to categories        |
| inventory      | 4581         | Physical DVD inventory items             |
| language       | 6            | Film languages                           |
| payment        | 14596        | Customer payments                        |
| rental         | 16044        | Rental transaction records               |
| staff          | 2            | Store staff                              |
| store          | 2            | Physical store locations                 |

Note: Pagila ships two data loading styles. `pagila-data.sql` uses `COPY` statements
(bulk-load, faster). `pagila-insert-data.sql` uses `INSERT` statements (slower but
portable). The provisioning script uses `pagila-data.sql`.

## E2E Validation Subset

For Phase 54 end-to-end DP synthesis validation, the following 5-table subset is
used. This subset exercises FK relationships across three levels of the schema
hierarchy:

| Table     | Approx. Rows | Role in Subset                                  |
|-----------|--------------|--------------------------------------------------|
| customer  | 599          | Root entity with PII fields (name, email)        |
| address   | 603          | FK: customer -> address -> city -> country       |
| rental    | 16044        | Transactional facts referencing customer + inventory |
| inventory | 4581         | Junction: film -> store -> inventory -> rental   |
| film      | 1000         | Leaf catalogue entity referenced by inventory    |

This subset was chosen because it:
- Contains PII fields suitable for masking (customer.first_name, customer.email)
- Has a multi-level FK chain (3 hops: rental -> inventory -> film)
- Provides sufficient row volume for DP noise to be statistically meaningful
- Covers both narrow (customer: 599 rows) and wide (rental: 16044 rows) tables

## Checksum Reference

The provisioning script pins these SHA-256 checksums (computed 2026-03-24):

| File                | SHA-256                                                          |
|---------------------|------------------------------------------------------------------|
| pagila-schema.sql   | 8ce358e4c8014087b85296694a0893887bd7a4190e3ce407f2721b86b98e5707 |
| pagila-data.sql     | 880580fb2cd4daaa99f290ced264988cdd657b3158be63cd281466f796f6dbf2 |

If checksums fail, the upstream files have changed. Verify the new files from the
official repository before updating the script.
