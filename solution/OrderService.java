package bench.orders;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderService {
    private final JdbcTemplate jdbc;

    /**
     * 使用同一数据源完成订单写入与主键查询。
     * @param jdbc 订单数据访问对象
     */
    public OrderService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 以数据库唯一订单号合并重试请求，并保留已有订单主键。
     * @param request 本次同步的订单
     * @return 持久化订单主键
     */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public long sync(OrderRequest request) {
        // 唯一约束负责跨进程竞争，冲突更新同时覆盖首次创建与后续更新。
        jdbc.update("INSERT INTO orders(order_no, item_code, qty) VALUES (?, ?, ?) "
                        + "ON DUPLICATE KEY UPDATE item_code = VALUES(item_code), qty = VALUES(qty)",
                request.orderNo(), request.itemCode(), request.qty());
        // 当前事务仍持有订单行锁，读取结果不会被其他写入穿插替换。
        return jdbc.queryForObject("SELECT id FROM orders WHERE order_no = ?",
                Long.class, request.orderNo());
    }
}
