-- Academy trong BreakoutOS: chua bai giang cua ba phan Foundation, Customer, Growth.
--
-- Ly do (Anna chot 04/09/2026): noi dung khoa dang nam ben academy.daothihang.com
-- (GHL Memberships), tach roi khoi quyen hoc va tien do von da o BreakoutOS. Moi
-- lan them san pham lai phai dong bo tay giua hai noi, va da mot lan lam khach mua
-- tron ba phan chi vao duoc mot. Hien chi co mot khach dang hoc nen day la luc re
-- nhat de dua tat ca ve mot cho.
--
-- Nguon noi dung: giao trinh goc trong vault (wiki/projects/breakout). Script nap:
-- wiki/projects/breakout/code/tools/academy_seed.py, chay lai duoc nhieu lan (idempotent theo key).

BEGIN;

CREATE TABLE IF NOT EXISTS breakoutos.courses (
    key         TEXT PRIMARY KEY,          -- foundation | customer | growth
    title       TEXT NOT NULL,
    subtitle    TEXT NOT NULL DEFAULT '',
    part_key    TEXT NOT NULL,             -- khop "parts" trong configs/entitlements.json
    position    INT  NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('draft', 'published')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS breakoutos.course_modules (
    id          BIGSERIAL PRIMARY KEY,
    course_key  TEXT NOT NULL REFERENCES breakoutos.courses(key) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    title       TEXT NOT NULL,
    position    INT  NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (course_key, key)
);

CREATE TABLE IF NOT EXISTS breakoutos.course_lessons (
    id            BIGSERIAL PRIMARY KEY,
    module_id     BIGINT NOT NULL REFERENCES breakoutos.course_modules(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,
    title         TEXT NOT NULL,
    position      INT  NOT NULL DEFAULT 0,
    duration      TEXT NOT NULL DEFAULT '',
    format        TEXT NOT NULL DEFAULT 'Video',
    video_url     TEXT NOT NULL DEFAULT '',   -- Cloudflare Stream hoac nguon khac
    workbook_url  TEXT NOT NULL DEFAULT '',
    markdown_content TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
    source_path   TEXT NOT NULL DEFAULT '',   -- file goc trong vault, de doi soat
    content_hash  TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (module_id, key)
);

CREATE INDEX IF NOT EXISTS idx_course_lessons_module ON breakoutos.course_lessons (module_id, position);

CREATE TABLE IF NOT EXISTS breakoutos.lesson_progress (
    student_id      UUID   NOT NULL REFERENCES breakoutos.students(id) ON DELETE CASCADE,
    lesson_id       BIGINT NOT NULL REFERENCES breakoutos.course_lessons(id) ON DELETE CASCADE,
    status          TEXT   NOT NULL DEFAULT 'started' CHECK (status IN ('started', 'completed')),
    watched_seconds INT    NOT NULL DEFAULT 0,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (student_id, lesson_id)
);

CREATE INDEX IF NOT EXISTS idx_lesson_progress_student ON breakoutos.lesson_progress (student_id, updated_at DESC);

COMMENT ON TABLE breakoutos.courses IS
    'Khoa hoc trong BreakoutOS. part_key khop "parts" trong configs/entitlements.json, quyen xem lay tu do.';

COMMIT;
