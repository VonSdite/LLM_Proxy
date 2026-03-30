BEGIN TRANSACTION;

UPDATE users
SET
    created_at = datetime(created_at, 'localtime') || '.000000',
    updated_at = datetime(updated_at, 'localtime') || '.000000'
WHERE created_at IS NOT NULL OR updated_at IS NOT NULL;

UPDATE request_logs
SET
    start_time = datetime(start_time, 'localtime') || '.000000',
    end_time = datetime(end_time, 'localtime') || '.000000',
    created_at = datetime(created_at, 'localtime') || '.000000'
WHERE start_time IS NOT NULL OR end_time IS NOT NULL OR created_at IS NOT NULL;

UPDATE daily_request_stats
SET
    created_at = datetime(created_at, 'localtime') || '.000000',
    updated_at = datetime(updated_at, 'localtime') || '.000000'
WHERE created_at IS NOT NULL OR updated_at IS NOT NULL;

COMMIT;
