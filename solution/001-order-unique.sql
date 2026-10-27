-- 所有数据与索引检查通过后才开始DDL；不删除或合并历史数据。
DROP PROCEDURE IF EXISTS migrate_order_unique;
DELIMITER //
CREATE PROCEDURE migrate_order_unique()
BEGIN
    IF EXISTS (SELECT 1 FROM orders GROUP BY order_no HAVING COUNT(*) > 1) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Duplicate business keys in orders; migration aborted';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='orders' AND index_name='uk_orders_order_no')
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='orders' AND index_name='uk_orders_order_no'
           GROUP BY index_name
           HAVING MIN(non_unique)=0 AND MAX(non_unique)=0 AND COUNT(*)=1
              AND GROUP_CONCAT(column_name ORDER BY seq_in_index)='order_no' AND COUNT(sub_part)=0
       ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unexpected index definition: uk_orders_order_no';
    END IF;
    IF EXISTS (SELECT 1 FROM production_plans GROUP BY order_id HAVING COUNT(*) > 1) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Duplicate business keys in production_plans; migration aborted';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='production_plans' AND index_name='uk_plan_order')
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='production_plans' AND index_name='uk_plan_order'
           GROUP BY index_name
           HAVING MIN(non_unique)=0 AND MAX(non_unique)=0 AND COUNT(*)=1
              AND GROUP_CONCAT(column_name ORDER BY seq_in_index)='order_id' AND COUNT(sub_part)=0
       ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unexpected index definition: uk_plan_order';
    END IF;
    IF EXISTS (SELECT 1 FROM process_details GROUP BY plan_id,step_no HAVING COUNT(*) > 1) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Duplicate business keys in process_details; migration aborted';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='process_details' AND index_name='uk_detail_step')
       AND NOT EXISTS (
           SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='process_details' AND index_name='uk_detail_step'
           GROUP BY index_name
           HAVING MIN(non_unique)=0 AND MAX(non_unique)=0 AND COUNT(*)=2
              AND GROUP_CONCAT(column_name ORDER BY seq_in_index)='plan_id,step_no' AND COUNT(sub_part)=0
       ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unexpected index definition: uk_detail_step';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='orders' AND index_name='uk_orders_order_no') THEN
        ALTER TABLE orders ADD UNIQUE KEY uk_orders_order_no (order_no);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='production_plans' AND index_name='uk_plan_order') THEN
        ALTER TABLE production_plans ADD UNIQUE KEY uk_plan_order (order_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='process_details' AND index_name='uk_detail_step') THEN
        ALTER TABLE process_details ADD UNIQUE KEY uk_detail_step (plan_id,step_no);
    END IF;
END//
DELIMITER ;
CALL migrate_order_unique();
DROP PROCEDURE migrate_order_unique;
