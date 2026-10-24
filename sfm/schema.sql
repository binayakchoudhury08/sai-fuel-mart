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

-- ================================================================ v2.1 additions (restored v1 features)
-- One credit-transport register row = diesel + cash taken by the driver, with a per-day SL No.
ALTER TABLE pump.party_txns ADD COLUMN IF NOT EXISTS cash_taken NUMERIC(14,2) NOT NULL DEFAULT 0;
ALTER TABLE pump.party_txns ADD COLUMN IF NOT EXISTS sl_no INTEGER;
ALTER TABLE pump.party_txns ADD COLUMN IF NOT EXISTS pay_for TEXT;
ALTER TABLE pump.party_txns ADD COLUMN IF NOT EXISTS corrected_from INTEGER;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'party_txns_cash_taken_chk') THEN
        ALTER TABLE pump.party_txns ADD CONSTRAINT party_txns_cash_taken_chk
            CHECK (cash_taken >= 0 AND cash_taken <= debit);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS party_txns_sl_idx ON pump.party_txns (business_date, sl_no);

-- per-litre discount printed on the transport bill (old Settings → Transporter discounts)
ALTER TABLE pump.parties ADD COLUMN IF NOT EXISTS discount_per_litre NUMERIC(8,2) NOT NULL DEFAULT 0;

-- detailed tanker receipt (old Fuel Receipts page)
ALTER TABLE pump.fuel_receipts ADD COLUMN IF NOT EXISTS order_no TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.fuel_receipts ADD COLUMN IF NOT EXISTS carrier_no TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.fuel_receipts ADD COLUMN IF NOT EXISTS carrier_name TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.fuel_receipts ADD COLUMN IF NOT EXISTS po_no TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.fuel_receipts ADD COLUMN IF NOT EXISTS po_date DATE;
ALTER TABLE pump.fuel_receipts ADD COLUMN IF NOT EXISTS water_checked TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.fuel_receipts ADD COLUMN IF NOT EXISTS compartments JSONB;
ALTER TABLE pump.fuel_receipts ADD COLUMN IF NOT EXISTS receipt_group TEXT;
ALTER TABLE pump.fuel_receipts ADD COLUMN IF NOT EXISTS dip_variance_mm NUMERIC(10,2);

-- structured proofs (dip check / nozzle testing / stock status, photo + video)
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS fuel_code TEXT;
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS stock_status TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS video_url TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS proof_time TEXT NOT NULL DEFAULT '';

ALTER TABLE pump.staff ADD COLUMN IF NOT EXISTS department TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.staff ADD COLUMN IF NOT EXISTS shift TEXT NOT NULL DEFAULT '';

-- ================================================================ v2.2: indexes for years of data
CREATE INDEX IF NOT EXISTS nozzle_readings_nozzle_date_idx ON pump.nozzle_readings (nozzle_id, business_date DESC);
CREATE INDEX IF NOT EXISTS party_txns_date_kind_idx ON pump.party_txns (business_date, kind) WHERE NOT voided;
CREATE INDEX IF NOT EXISTS party_txns_party_live_idx ON pump.party_txns (party_id) INCLUDE (debit, credit) WHERE NOT voided;
CREATE INDEX IF NOT EXISTS attendance_date_idx ON pump.attendance (work_date);
CREATE INDEX IF NOT EXISTS staff_payments_month_idx ON pump.staff_payments (for_month);
CREATE INDEX IF NOT EXISTS staff_payments_date_idx ON pump.staff_payments (business_date);
CREATE INDEX IF NOT EXISTS expenses_date_idx ON pump.expenses (business_date);
CREATE INDEX IF NOT EXISTS fuel_receipts_date_idx ON pump.fuel_receipts (business_date);
CREATE INDEX IF NOT EXISTS tank_dips_tank_date_idx ON pump.tank_dips (tank_id, business_date DESC);
CREATE INDEX IF NOT EXISTS audit_date_idx ON pump.audit_log (business_date);
CREATE INDEX IF NOT EXISTS audit_user_idx ON pump.audit_log (username, id DESC);
CREATE INDEX IF NOT EXISTS proofs_date_idx ON pump.proofs (business_date, category);
CREATE INDEX IF NOT EXISTS day_collections_date_idx ON pump.day_collections (business_date);
-- refresh planner statistics after big imports (cheap, safe to run on every start)
ANALYZE pump.party_txns;
ANALYZE pump.nozzle_readings;

-- ================================================================ v2.3: live camera proofs
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS capture_mode TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS captured_at TIMESTAMPTZ;
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS gps_accuracy_m NUMERIC(8,1);
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS device TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS photo_path TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS video_path TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS photo_bytes INTEGER;
ALTER TABLE pump.proofs ADD COLUMN IF NOT EXISTS video_bytes INTEGER;

-- ================================================================ v3.0: daily-closing credit totals per transporter
-- What the closing person writes for each transporter for the day; reconciled against the slip entries.
CREATE TABLE IF NOT EXISTS pump.day_credit (
    business_date DATE NOT NULL,
    party_id      INTEGER NOT NULL REFERENCES pump.parties(id),
    amount        NUMERIC(14,2) NOT NULL CHECK (amount >= 0),
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (business_date, party_id)
);
DROP TRIGGER IF EXISTS day_credit_lockguard ON pump.day_credit;
CREATE TRIGGER day_credit_lockguard BEFORE INSERT OR UPDATE OR DELETE ON pump.day_credit
    FOR EACH ROW EXECUTE FUNCTION pump.guard_locked_day();

-- live-photo attendance
ALTER TABLE pump.attendance ADD COLUMN IF NOT EXISTS photo_path TEXT NOT NULL DEFAULT '';
ALTER TABLE pump.attendance ADD COLUMN IF NOT EXISTS captured_at TIMESTAMPTZ;
ALTER TABLE pump.attendance ADD COLUMN IF NOT EXISTS latitude TEXT;
ALTER TABLE pump.attendance ADD COLUMN IF NOT EXISTS longitude TEXT;
ALTER TABLE pump.attendance ADD COLUMN IF NOT EXISTS marked_via TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE pump.attendance ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT '';

-- v3.0: Admin role
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'users_role_check'
               AND pg_get_constraintdef(oid) NOT LIKE '%admin%') THEN
        ALTER TABLE pump.users DROP CONSTRAINT users_role_check;
        ALTER TABLE pump.users ADD CONSTRAINT users_role_check CHECK (role IN ('owner','admin','manager','operator'));
    END IF;
END $$;

-- ================================================================ v3.1: temporary access grants (owner → a user)
CREATE TABLE IF NOT EXISTS pump.access_grants (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES pump.users(id),
    perms         TEXT NOT NULL,                 -- comma-separated permission keys
    date_from     DATE NOT NULL,
    date_to       DATE NOT NULL,
    valid_until   TIMESTAMPTZ NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    reopened_days DATE[] NOT NULL DEFAULT '{}',  -- locked days this grant opened; locked again when it ends
    created_by    TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at    TIMESTAMPTZ, revoked_by TEXT,
    closed_at     TIMESTAMPTZ,                   -- when its days were locked again
    CHECK (date_to >= date_from)
);
CREATE INDEX IF NOT EXISTS access_grants_user_idx ON pump.access_grants (user_id, valid_until);

-- v3.2: attendance check-out by live photo
ALTER TABLE pump.attendance ADD COLUMN IF NOT EXISTS checkout_at TIMESTAMPTZ;
ALTER TABLE pump.attendance ADD COLUMN IF NOT EXISTS checkout_photo_path TEXT NOT NULL DEFAULT '';

-- ================================================================ v3.4: credit transport slips are never locked (last 31 days)
-- Slip entries (fuel / cash taken) may be added, edited or deleted on any day of the last 31 days, even when the
-- Daily closing of that day is locked. Every other kind of entry stays protected by the locked-day guard.
CREATE OR REPLACE FUNCTION pump.guard_locked_day() RETURNS trigger AS $$
DECLARE d DATE; k TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN d := OLD.business_date; ELSE d := NEW.business_date; END IF;
    IF TG_TABLE_NAME = 'party_txns' THEN
        IF TG_OP = 'DELETE' THEN k := OLD.kind; ELSE k := NEW.kind; END IF;
        IF k IN ('fuel', 'cash_advance') AND d >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date - 31 THEN
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END IF;
    END IF;
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

-- ================================================================ v3.5: credit transport slips are never locked (any date)
CREATE OR REPLACE FUNCTION pump.guard_locked_day() RETURNS trigger AS $$
DECLARE d DATE; k TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN d := OLD.business_date; ELSE d := NEW.business_date; END IF;
    IF TG_TABLE_NAME = 'party_txns' THEN
        IF TG_OP = 'DELETE' THEN k := OLD.kind; ELSE k := NEW.kind; END IF;
        IF k IN ('fuel', 'cash_advance') THEN       -- credit transport slips: any date, any day status
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END IF;
    END IF;
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
