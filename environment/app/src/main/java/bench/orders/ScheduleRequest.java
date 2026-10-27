package bench.orders;

import java.util.List;
import javax.validation.constraints.NotEmpty;
import javax.validation.constraints.NotNull;
import javax.validation.constraints.Positive;
import javax.validation.constraints.Size;

public record ScheduleRequest(
        @NotEmpty @Size(max = 32) List<@NotNull @Positive Long> planIds) {
}
