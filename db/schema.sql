-- Waktu disimpan dalam UTC (TIMESTAMPTZ). Konversi ke WIB hanya di lapisan tampilan.

CREATE TABLE IF NOT EXISTS measurements (
    id            BIGSERIAL PRIMARY KEY,
    machine_id    TEXT        NOT NULL,
    unit_id       TEXT        NOT NULL,
    item_ukur     TEXT        NOT NULL,
    measured_at   TIMESTAMPTZ NOT NULL,
    value         NUMERIC     NOT NULL,
    nominal       NUMERIC,
    usl           NUMERIC,
    lsl           NUMERIC,
    ratio         NUMERIC,                 -- NULL jika zone = NO_STANDARD
    zone          TEXT        NOT NULL
                  CHECK (zone IN ('OK', 'WARNING', 'NG', 'NO_STANDARD')),
    source_batch  TEXT,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_measurements_key
        UNIQUE (machine_id, unit_id, item_ukur, measured_at)
);

CREATE INDEX IF NOT EXISTS ix_measurements_measured_at ON measurements (measured_at DESC);
CREATE INDEX IF NOT EXISTS ix_measurements_zone        ON measurements (zone);
CREATE INDEX IF NOT EXISTS ix_measurements_machine_item ON measurements (machine_id, item_ukur);

CREATE TABLE IF NOT EXISTS spec_master (
    id             BIGSERIAL PRIMARY KEY,
    machine_id     TEXT        NOT NULL,
    item_ukur      TEXT        NOT NULL,
    nominal        NUMERIC,
    usl            NUMERIC,
    lsl            NUMERIC,
    ref_mode       TEXT,                    -- aturan referensi, mis. untuk item batas bawah saja (belum diputuskan)
    berlaku_sejak  TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_spec_master_key
        UNIQUE (machine_id, item_ukur, berlaku_sejak)
);
