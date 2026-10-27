-- 独立演示数据，不包含真实订单。
INSERT INTO orders(id,order_no,item_code,qty) VALUES
 (1,'DEMO-HISTORY-001','M-A',12.5000),
 (2,'DEMO-COMPLETED-001','M-A',5.0000);
INSERT INTO production_plans(id,order_id,item_code,qty,status) VALUES
 (1,1,'M-A',12.5000,'WAITING'),(2,2,'M-A',5.0000,'COMPLETED');
INSERT INTO process_details(plan_id,step_no,process_code,qty) VALUES
 (2,1,'CUT',5.0000),(2,2,'PACK',5.0000);
