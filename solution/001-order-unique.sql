-- 先检查历史数据，禁止通过删除记录掩盖重复订单。
DROP PROCEDURE IF EXISTS migrate_order_unique;
DELIMITER //
CREATE PROCEDURE migrate_order_unique()
BEGIN
    IF EXISTS (SELECT 1 FROM orders GROUP BY order_no HAVING COUNT(*) > 1) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Duplicate order numbers exist; migration aborted without deleting data';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'orders'
          AND index_name = 'uk_orders_order_no'
    ) THEN
        ALTER TABLE orders ADD UNIQUE KEY uk_orders_order_no (order_no);
    ELSEIF (
        SELECT COUNT(*) FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'orders'
          AND index_name = 'uk_orders_order_no'
    ) <> 1 OR NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'orders'
          AND index_name = 'uk_orders_order_no' AND non_unique = 0
          AND column_name = 'order_no' AND sub_part IS NULL
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unexpected definition of uk_orders_order_no';
    END IF;
END//
DELIMITER ;
CALL migrate_order_unique();
DROP PROCEDURE migrate_order_unique;
