package bench.orders;

import java.util.List;
import javax.validation.Valid;
import javax.validation.constraints.NotEmpty;
import javax.validation.constraints.NotNull;
import javax.validation.constraints.Size;

public record BatchSyncRequest(
        @NotEmpty @Size(max = 32) List<@NotNull @Valid OrderRequest> orders,
        boolean autoSchedule) {
}
