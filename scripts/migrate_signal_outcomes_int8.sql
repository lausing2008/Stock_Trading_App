-- INT-8: Add multi-window forward return columns + research alignment to signal_outcomes
-- Run once on production DB after deploying updated models.py
-- Safe to re-run (uses IF NOT EXISTS pattern via DO block).

DO $$
BEGIN
    -- Multi-window forward returns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='price_5d') THEN
        ALTER TABLE signal_outcomes ADD COLUMN price_5d FLOAT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='return_5d') THEN
        ALTER TABLE signal_outcomes ADD COLUMN return_5d FLOAT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='is_correct_5d') THEN
        ALTER TABLE signal_outcomes ADD COLUMN is_correct_5d BOOLEAN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='price_10d') THEN
        ALTER TABLE signal_outcomes ADD COLUMN price_10d FLOAT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='return_10d') THEN
        ALTER TABLE signal_outcomes ADD COLUMN return_10d FLOAT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='is_correct_10d') THEN
        ALTER TABLE signal_outcomes ADD COLUMN is_correct_10d BOOLEAN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='price_20d') THEN
        ALTER TABLE signal_outcomes ADD COLUMN price_20d FLOAT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='return_20d') THEN
        ALTER TABLE signal_outcomes ADD COLUMN return_20d FLOAT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='is_correct_20d') THEN
        ALTER TABLE signal_outcomes ADD COLUMN is_correct_20d BOOLEAN;
    END IF;
    -- Research alignment at evaluation time
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='research_rec') THEN
        ALTER TABLE signal_outcomes ADD COLUMN research_rec VARCHAR(16);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='signal_outcomes' AND column_name='research_score') THEN
        ALTER TABLE signal_outcomes ADD COLUMN research_score FLOAT;
    END IF;
END $$;
