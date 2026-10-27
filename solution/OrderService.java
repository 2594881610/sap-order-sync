package bench.orders;

import java.math.BigDecimal;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

@Service
public class OrderService {
    private final JdbcTemplate jdbc;
    private static final Map<String, List<String>> ROUTES = Map.of(
            "M-A", List.of("CUT", "PACK"),
            "M-B", List.of("CUT", "DRILL", "PACK"));

    private record Order(long id, String orderNo) { }
    private record Plan(long id, String itemCode, BigDecimal qty, String status) { }

    /**
     * 注入订单及排产使用的同一数据源。
     * @param jdbc 数据访问对象
     */
    public OrderService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 同步一张订单并保留计划关联。
     * @param request 订单内容
     * @return 订单主键
     */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public long sync(OrderRequest request) {
        return syncBatch(new BatchSyncRequest(List.of(request), false)).get(0);
    }

    /**
     * 在同一事务中同步订单、计划及可选的自动排产。
     * @param request 批量请求
     * @return 按原始请求顺序排列的订单主键
     */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public List<Long> syncBatch(BatchSyncRequest request) {
        List<OrderRequest> entries = new ArrayList<>(request.orders());
        if (new HashSet<>(entries.stream().map(OrderRequest::orderNo).toList()).size() != entries.size()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "重复订单号");
        }
        entries.sort(Comparator.comparing(OrderRequest::orderNo));
        Map<String, Long> ids = new HashMap<>();
        for (OrderRequest entry : entries) {
            // 唯一键上的写入锁同时覆盖首次创建和已有订单，持有到整批提交。
            jdbc.update("INSERT INTO orders(order_no,item_code,qty) VALUES (?,?,?) "
                            + "ON DUPLICATE KEY UPDATE item_code=VALUES(item_code),qty=VALUES(qty)",
                    entry.orderNo(), entry.itemCode(), entry.qty());
            long orderId = jdbc.queryForObject("SELECT id FROM orders WHERE order_no=?", Long.class, entry.orderNo());
            List<Plan> plans = plans(orderId);
            Plan plan;
            if (plans.isEmpty()) {
                long planId = insert("INSERT INTO production_plans(order_id,item_code,qty,status) VALUES (?,?,?,'WAITING')",
                        orderId, entry.itemCode(), entry.qty());
                plan = new Plan(planId, entry.itemCode(), entry.qty(), "WAITING");
            } else {
                plan = plans.get(0);
                if ("COMPLETED".equals(plan.status())) {
                    throw conflict("已完成计划不可同步");
                }
                if ("WAITING".equals(plan.status())) {
                    jdbc.update("UPDATE production_plans SET item_code=?,qty=? WHERE id=?",
                            entry.itemCode(), entry.qty(), plan.id());
                    plan = new Plan(plan.id(), entry.itemCode(), entry.qty(), plan.status());
                } else {
                    jdbc.update("UPDATE production_plans SET qty=? WHERE id=?", entry.qty(), plan.id());
                }
            }
            if (request.autoSchedule()) {
                schedulePlan(plan);
            }
            ids.put(entry.orderNo(), orderId);
        }
        return request.orders().stream().map(e -> ids.get(e.orderNo())).toList();
    }

    /**
     * 对整批计划排产，已排产计划沿用原工序。
     * @param request 计划主键
     * @return 按请求顺序排列的计划主键
     */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public List<Long> schedule(ScheduleRequest request) {
        if (new HashSet<>(request.planIds()).size() != request.planIds().size()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "重复计划主键");
        }
        List<Order> orders = new ArrayList<>();
        for (long planId : request.planIds()) {
            List<Order> found = jdbc.query(
                    "SELECT o.id,o.order_no FROM orders o JOIN production_plans p ON p.order_id=o.id WHERE p.id=?",
                    (rs, row) -> new Order(rs.getLong(1), rs.getString(2)), planId);
            if (found.isEmpty()) {
                throw conflict("计划不存在");
            }
            orders.add(found.get(0));
        }
        // 两个入口都先按订单号锁订单，再读取计划最新状态，避免交叉锁序。
        orders.sort(Comparator.comparing(Order::orderNo));
        for (Order order : orders) {
            jdbc.queryForObject("SELECT id FROM orders WHERE id=? FOR UPDATE", Long.class, order.id());
        }
        for (long planId : request.planIds()) {
            Plan plan = jdbc.query("SELECT id,item_code,qty,status FROM production_plans WHERE id=?",
                    (rs, row) -> new Plan(rs.getLong(1), rs.getString(2), rs.getBigDecimal(3), rs.getString(4)), planId).get(0);
            schedulePlan(plan);
        }
        return request.planIds();
    }

    /**
     * 查询订单关联的计划。
     * @param orderId 订单主键
     * @return 计划列表
     */
    private List<Plan> plans(long orderId) {
        return jdbc.query("SELECT id,item_code,qty,status FROM production_plans WHERE order_id=? ORDER BY id",
                (rs, row) -> new Plan(rs.getLong(1), rs.getString(2), rs.getBigDecimal(3), rs.getString(4)), orderId);
    }

    /**
     * 将待排产计划展开为路线工序，工序数量为排产时快照。
     * @param plan 当前计划
     */
    private void schedulePlan(Plan plan) {
        if ("COMPLETED".equals(plan.status())) {
            throw conflict("已完成计划不可排产");
        }
        if ("SCHEDULED".equals(plan.status())) {
            return;
        }
        List<String> route = ROUTES.get(plan.itemCode());
        if (route == null) {
            throw conflict("物料缺少工艺路线");
        }
        for (int step = 0; step < route.size(); step++) {
            jdbc.update("INSERT INTO process_details(plan_id,step_no,process_code,qty) VALUES (?,?,?,?)",
                    plan.id(), step + 1, route.get(step), plan.qty());
        }
        jdbc.update("UPDATE production_plans SET status='SCHEDULED' WHERE id=?", plan.id());
    }

    /**
     * 写入记录并读取数据库分配的主键。
     * @param sql 插入语句
     * @param values 参数值
     * @return 新主键
     */
    private long insert(String sql, Object... values) {
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbc.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS);
            for (int index = 0; index < values.length; index++) {
                statement.setObject(index + 1, values[index]);
            }
            return statement;
        }, keys);
        return keys.getKey().longValue();
    }

    /**
     * 将业务状态冲突映射为HTTP 409并触发事务回滚。
     * @param message 冲突原因
     * @return 业务异常
     */
    private ResponseStatusException conflict(String message) {
        return new ResponseStatusException(HttpStatus.CONFLICT, message);
    }
}
