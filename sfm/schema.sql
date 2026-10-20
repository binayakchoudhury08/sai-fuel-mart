-- Sai Fuel Mart v2 schema. Lives in its own Postgres schema "pump" so the
-- old tables in "public" are never touched. Every statement is idempotent:
-- this file runs on every app start and only creates what is missing.

CREATE SCHEMA IF NOT EXISTS pump;

-- ---------------------------------------------------------------- users
CREATE TABLE IF NOT EXISTS pump.users (
    id              SERIAL PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    full_name       TEXT NOT NULL DEFAULT '',
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('owner','manager','operator')),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TIMESTAMPTZ,
    must_change_pw  BOOLEAN NOT NULL DEFAULT FALSE,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pump.settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pump.counters (
    name  TEXT PRIMARY KEY,
    value BIGINT NOT NULL DEFAULT 0
);

-- one row per submitted form; a second submit of the same form is rejected
CREATE TABLE IF NOT EXISTS pump.form_tokens (
    token   TEXT PRIMARY KEY,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------- fuel
CREATE TABLE IF NOT EXISTS pump.fuels (
    code   TEXT PRIMARY KEY,
    name   TEXT NOT NULL,
    unit   TEXT NOT NULL DEFAULT 'L',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    sort   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pump.fuel_rates (
    id             SERIAL PRIMARY KEY,
    fuel_code      TEXT NOT NULL REFERENCES pump.fuels(code),
    effective_from DATE NOT NULL,
    rate           NUMERIC(10,2) NOT NULL CHECK (rate >= 0),
    created_by     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (fuel_code, effective_from)
);

CREATE TABLE IF NOT EXISTS pump.tanks (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    fuel_code   TEXT NOT NULL REFERENCES pump.fuels(code),
    capacity_l  NUMERIC(12,2) NOT NULL DEFAULT 0,
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS pump.nozzles (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    machine_no TEXT NOT NULL DEFAULT '',
    fuel_code  TEXT NOT NULL REFERENCES pump.fuels(code),
    tank_id    INTEGER REFERENCES pump.tanks(id),
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    sort       INTEGER NOT NULL DEFAULT 0,
    legacy_id  INTEGER
);

-- ---------------------------------------------------------------- day
CREATE TABLE IF NOT EXISTS pump.days (
    business_date DATE PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','submitted','locked')),
    counted_cash  NUMERIC(14,2),
    denominations JSONB,
    notes         TEXT NOT NULL DEFAULT '',
    snapshot      JSONB,
    is_legacy     BOOLEAN NOT NULL DEFAULT FALSE,
    legacy_totals JSONB,
    submitted_by  TEXT, submitted_at TIMESTAMPTZ,
    locked_by     TEXT, locked_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pump.nozzle_readings (
    id              SERIAL PRIMARY KEY,
    business_date   DATE NOT NULL,
    nozzle_id       INTEGER NOT NULL REFERENCES pump.nozzles(id),
    opening         NUMERIC(14,2) NOT NULL,
    closing         NUMERIC(14,2) NOT NULL,
    testing         NUMERIC(10,2) NOT NULL DEFAULT 0,
    rate            NUMERIC(10,2) NOT NULL,
    opening_override_reason TEXT,
    updated_by      TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (business_date, nozzle_id),
    CHECK (closing >= opening),
    CHECK (testing >= 0)
);

CREATE TABLE IF NOT EXISTS pump.collection_modes (
    id     SERIAL PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    sort   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pump.day_collections (
    business_date DATE NOT NULL,
    mode_id       INTEGER NOT NULL REFERENCES pump.collection_modes(id),
    amount        NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (amount >= 0),
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (business_date, mode_id)
);

CREATE TABLE IF NOT EXISTS pump.expenses (
    id            SERIAL PRIMARY KEY,
    business_date DATE NOT NULL,
    category      TEXT NOT NULL DEFAULT 'General',
    description   TEXT NOT NULL DEFAULT '',
    amount        NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    from_counter  BOOLEAN NOT NULL DEFAULT TRUE,
    created_by    TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    voided        BOOLEAN NOT NULL DEFAULT FALSE,
    void_reason   TEXT, voided_by TEXT, voided_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------- parties
CREATE TABLE IF NOT EXISTS pump.parties (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    mobile        TEXT NOT NULL DEFAULT '',
    vehicles      TEXT NOT NULL DEFAULT '',
    address       TEXT NOT NULL DEFAULT '',
    gstin         TEXT NOT NULL DEFAULT '',
    credit_limit  NUMERIC(14,2) NOT NULL DEFAULT 0,
    bill_discount NUMERIC(14,2) NOT NULL DEFAULT 0,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    legacy_id     INTEGER,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS parties_name_uniq ON pump.parties (LOWER(TRIM(name)));

-- The ONLY place a party's money moves. Balance = SUM(debit) - SUM(credit)
-- over rows that are not voided. Nothing stores a running total.
CREATE TABLE IF NOT EXISTS pump.party_txns (
    id            SERIAL PRIMARY KEY,
    party_id      INTEGER NOT NULL REFERENCES pump.parties(id),
    business_date DATE NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN
                  ('opening','fuel','lube','cash_advance','payment','discount','adjustment')),
    fuel_code     TEXT REFERENCES pump.fuels(code),
    lube_product_id INTEGER,
    qty           NUMERIC(12,2) NOT NULL DEFAULT 0,
    rate          NUMERIC(10,2) NOT NULL DEFAULT 0,
    debit         NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit        NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
    pay_mode      TEXT,
    reference     TEXT NOT NULL DEFAULT '',
    vehicle_no    TEXT NOT NULL DEFAULT '',
    challan_no    TEXT NOT NULL DEFAULT '',
    slip_no       TEXT NOT NULL DEFAULT '',
    receipt_no    TEXT UNIQUE,
    remarks       TEXT NOT NULL DEFAULT '',
    created_by    TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    edited_by     TEXT, edited_at  TIMESTAMPTZ,
    voided        BOOLEAN NOT NULL DEFAULT FALSE,
    void_reason   TEXT, voided_by TEXT, voided_at TIMESTAMPTZ,
    is_legacy     BOOLEAN NOT NULL DEFAULT FALSE,
    CHECK (debit = 0 OR credit = 0)
);
CREATE INDEX IF NOT EXISTS party_txns_party_idx ON pump.party_txns (party_id, business_date, id);
CREATE INDEX IF NOT EXISTS party_txns_date_idx  ON pump.party_txns (business_date);

-- ---------------------------------------------------------------- lube
CREATE TABLE IF NOT EXISTS pump.lube_products (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    unit          TEXT NOT NULL DEFAULT 'pcs',
    sale_rate     NUMERIC(10,2) NOT NULL DEFAULT 0,
    reorder_level NUMERIC(10,2) NOT NULL DEFAULT 5,
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    legacy_id     INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS lube_products_name_uniq ON pump.lube_products (LOWER(TRIM(name)));

-- stock = SUM(qty) of non-voided moves; sales are negative qty
CREATE TABLE IF NOT EXISTS pump.lube_moves (
    id            SERIAL PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES pump.lube_products(id),
    business_date DATE NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('opening','purchase','sale','return','adjust')),
    qty           NUMERIC(12,2) NOT NULL,
    rate          NUMERIC(10,2) NOT NULL DEFAULT 0,
    amount        NUMERIC(14,2) NOT NULL DEFAULT 0,
    pay_mode      TEXT,
    party_txn_id  INTEGER REFERENCES pump.party_txns(id),
    remarks       TEXT NOT NULL DEFAULT '',
    created_by    TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    voided        BOOLEAN NOT NULL DEFAULT FALSE,
    void_reason   TEXT, voided_by TEXT, voided_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS lube_moves_idx ON pump.lube_moves (product_id, business_date);
CREATE INDEX IF NOT EXISTS lube_moves_date_idx ON pump.lube_moves (business_date);

-- ---------------------------------------------------------------- tanks
CREATE TABLE IF NOT EXISTS pump.tank_dips (
    id            SERIAL PRIMARY KEY,
    business_date DATE NOT NULL,
    tank_id       INTEGER NOT NULL REFERENCES pump.tanks(id),
    dip_mm        NUMERIC(10,2) NOT NULL DEFAULT 0,
    volume_l      NUMERIC(12,2) NOT NULL,
    water_mm      NUMERIC(10,2) NOT NULL DEFAULT 0,
    temperature_c NUMERIC(6,2),
    density       NUMERIC(8,2),
    source        TEXT NOT NULL DEFAULT 'manual',
    atg           JSONB,
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (business_date, tank_id)
);

CREATE TABLE IF NOT EXISTS pump.fuel_receipts (
    id            SERIAL PRIMARY KEY,
    business_date DATE NOT NULL,
    tank_id       INTEGER NOT NULL REFERENCES pump.tanks(id),
    invoice_no    TEXT NOT NULL DEFAULT '',
    vehicle_no    TEXT NOT NULL DEFAULT '',
    invoice_qty_l NUMERIC(12,2) NOT NULL DEFAULT 0,
    received_qty_l NUMERIC(12,2) NOT NULL CHECK (received_qty_l >= 0),
    dip_before_mm NUMERIC(10,2),
    dip_after_mm  NUMERIC(10,2),
    challan_density NUMERIC(8,2),
    observed_density NUMERIC(8,2),
    temperature_c NUMERIC(6,2),
    invoice_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    remarks       TEXT NOT NULL DEFAULT '',
    created_by    TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    voided        BOOLEAN NOT NULL DEFAULT FALSE,
    void_reason   TEXT, voided_by TEXT, voided_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------- staff
CREATE TABLE IF NOT EXISTS pump.staff (
    id             SERIAL PRIMARY KEY,
    emp_code       TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    phone          TEXT NOT NULL DEFAULT '',
    designation    TEXT NOT NULL DEFAULT '',
    monthly_salary NUMERIC(12,2) NOT NULL DEFAULT 0,
    joined_on      DATE,
    bank_account   TEXT NOT NULL DEFAULT '',
    active         BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS pump.attendance (
    staff_id   INTEGER NOT NULL REFERENCES pump.staff(id),
    work_date  DATE NOT NULL,
    status     TEXT NOT NULL CHECK (status IN ('P','H','A','L','O')),
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (staff_id, work_date)
);

CREATE TABLE IF NOT EXISTS pump.staff_payments (
    id            SERIAL PRIMARY KEY,
    staff_id      INTEGER NOT NULL REFERENCES pump.staff(id),
    business_date DATE NOT NULL,
    for_month     TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('advance','salary','bonus')),
    amount        NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    pay_mode      TEXT NOT NULL DEFAULT 'Cash from counter',
    remarks       TEXT NOT NULL DEFAULT '',
    created_by    TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    voided        BOOLEAN NOT NULL DEFAULT FALSE,
    void_reason   TEXT, voided_by TEXT, voided_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------- proofs
CREATE TABLE IF NOT EXISTS pump.proofs (
    id            SERIAL PRIMARY KEY,
    business_date DATE NOT NULL,
    category      TEXT NOT NULL DEFAULT 'General',
    note          TEXT NOT NULL DEFAULT '',
    file_url      TEXT NOT NULL DEFAULT '',
    latitude      TEXT, longitude TEXT,
    created_by    TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------- audit
CREATE TABLE IF NOT EXISTS pump.audit_log (
    id            BIGSERIAL PRIMARY KEY,
    at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    username      TEXT, role TEXT, ip TEXT,
    action        TEXT NOT NULL,
    entity        TEXT NOT NULL,
    entity_id     TEXT,
    business_date DATE,
    summary       TEXT NOT NULL DEFAULT '',
    before_data   JSONB,
    after_data    JSONB
);
CREATE INDEX IF NOT EXISTS audit_at_idx ON pump.audit_log (at DESC);

-- Tamper guards: the app can never rewrite history.
CREATE OR REPLACE FUNCTION pump.forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Records in % cannot be %d. Use void instead.', TG_TABLE_NAME, LOWER(TG_OP);
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_log_readonly ON pump.audit_log;
CREATE TRIGGER audit_log_readonly BEFORE UPDATE OR DELETE ON pump.audit_log
    FOR EACH ROW EXECUTE FUNCTION pump.forbid_change();

DROP TRIGGER IF EXISTS party_txns_nodelete ON pump.party_txns;
CREATE TRIGGER party_txns_nodelete BEFORE DELETE ON pump.party_txns
    FOR EACH ROW EXECUTE FUNCTION pump.forbid_change();

DROP TRIGGER IF EXISTS lube_moves_nodelete ON pump.lube_moves;
CREATE TRIGGER lube_moves_nodelete BEFORE DELETE ON pump.lube_moves
    FOR EACH ROW EXECUTE FUNCTION pump.forbid_change();

DROP TRIGGER IF EXISTS expenses_nodelete ON pump.expenses;
CREATE TRIGGER expenses_nodelete BEFORE DELETE ON pump.expenses
    FOR EACH ROW EXECUTE FUNCTION pump.forbid_change();

DROP TRIGGER IF EXISTS staff_payments_nodelete ON pump.staff_payments;
CREATE TRIGGER staff_payments_nodelete BEFORE DELETE ON pump.staff_payments
    FOR EACH ROW EXECUTE FUNCTION pump.forbid_change();

DROP TRIGGER IF EXISTS fuel_receipts_nodelete ON pump.fuel_receipts;
CREATE TRIGGER fuel_receipts_nodelete BEFORE DELETE ON pump.fuel_receipts
    FOR EACH ROW EXECUTE FUNCTION pump.forbid_change();

-- ---------------------------------------------------------------- seed
INSERT INTO pump.fuels (code, name, unit, sort) VALUES
    ('MS',  'Petrol (MS)', 'L', 1),
    ('HSD', 'Diesel (HSD)', 'L', 2),
    ('CNG', 'CNG', 'kg', 3)
ON CONFLICT (code) DO NOTHING;

INSERT INTO pump.collection_modes (name, sort) VALUES
    ('PhonePe', 1), ('Card swipe', 2), ('HP Pay', 3), ('HPCL OTP / Fleet card', 4), ('Other UPI', 5)
ON CONFLICT (name) DO NOTHING;

INSERT INTO pump.settings (key, value) VALUES
    ('station_name', 'Sai Fuel Mart'),
    ('station_address', ''),
    ('station_phone', ''),
    ('station_gstin', ''),
    ('backdate_days', '2'),
    ('cash_tolerance', '100'),
    ('variance_alert_pct', '0.6')
ON CONFLICT (key) DO NOTHING;

-- Locked days are enforced by the database itself, not only the app.
CREATE OR REPLACE FUNCTION pump.guard_locked_day() RETURNS trigger AS $$
DECLARE d DATE;
BEGIN
    IF TG_OP = 'DELETE' THEN d := OLD.business_date; ELSE d := NEW.business_date; END IF;
    IF EXISTS (SELECT 1 FROM pump.days WHERE business_date = d AND status = 'locked') THEN
        RAISE EXCEPTION 'Day % is locked. Ask the owner to unlock it first.', d;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.business_date <> NEW.business_date AND
       EXISTS (SELECT 1 FROM pump.days WHERE business_date = OLD.business_date AND status = 'locked') THEN
        RAISE EXCEPTION 'Day % is locked. Ask the owner to unlock it first.', OLD.business_date;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END; $$ LANGUAGE plpgsql;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['nozzle_readings','day_collections','expenses','party_txns',
                             'lube_moves','fuel_receipts','staff_payments','tank_dips']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON pump.%I', t || '_lockguard', t);
        EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OR DELETE ON pump.%I
                        FOR EACH ROW EXECUTE FUNCTION pump.guard_locked_day()', t || '_lockguard', t);
    END LOOP;
END $$;
