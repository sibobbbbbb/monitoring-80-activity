-- Waktu disimpan dalam UTC (TIMESTAMPTZ). Konversi ke WIB hanya di lapisan tampilan.
--
-- Struktur mengikuti export FEXQMS: Part + Operation ("Jenis Item Cek") -> Characteristics
-- ("Item Cek") -> Machine -> pengukuran per waktu. value = kolom XChart (BUKAN "Mea. Data : 1").
-- usl/lsl berasal dari header file per kombinasi Part+Operation+Machine+Characteristics,
-- disimpan per baris agar riwayat tetap benar bila standar berubah.
--
-- Catatan migrasi: file ini hanya dijalankan Postgres pada volume kosong. Setelah skema berubah,
-- volume lama harus dibuat ulang (docker compose down -v).

CREATE TABLE IF NOT EXISTS measurements (
    id              BIGSERIAL PRIMARY KEY,
    part            TEXT        NOT NULL,
    operation       TEXT        NOT NULL,
    machine         TEXT        NOT NULL,
    characteristics TEXT        NOT NULL,
    measured_at     TIMESTAMPTZ NOT NULL,
    value           NUMERIC     NOT NULL,   -- XChart
    nominal         NUMERIC,                -- opsional; ref = nominal, atau 0 bila kosong
    usl             NUMERIC,
    lsl             NUMERIC,
    ratio           NUMERIC,                -- NULL jika zone = NO_STANDARD
    zone            TEXT        NOT NULL
                    CHECK (zone IN ('OK', 'WARNING', 'NG', 'NO_STANDARD')),
    source_batch    TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_measurements_key
        UNIQUE (part, operation, machine, characteristics, measured_at)
);

CREATE INDEX IF NOT EXISTS ix_measurements_measured_at ON measurements (measured_at DESC);

CREATE TABLE IF NOT EXISTS spec_master (
    id              BIGSERIAL PRIMARY KEY,
    part            TEXT        NOT NULL,
    operation       TEXT        NOT NULL,
    machine         TEXT        NOT NULL,
    characteristics TEXT        NOT NULL,
    nominal         NUMERIC,
    usl             NUMERIC,
    lsl             NUMERIC,
    berlaku_sejak   TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_spec_master_key
        UNIQUE (part, operation, machine, characteristics, berlaku_sejak)
);
