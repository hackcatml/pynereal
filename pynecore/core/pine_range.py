def pine_range(from_num: int | float, to_num: int | float, step_num: int | float | None = None):
    """
    Emulates Pine Script's for loop range behavior.

    :param from_num: Start value (inclusive) - can be int, float, or Series
    :param to_num: End value (inclusive) - can be int, float, or Series
    :param step_num: Step value (optional, defaults to +1/-1 based on direction) - can be int, float, or Series
    :return: A generator that yields values from from_num to to_num (inclusive)
    :raises ValueError: If step_num is zero
    """
    # Import Series here to avoid circular imports
    from pynecore.types.series import Series

    # Extract values from Series if needed
    from_val = from_num[0] if isinstance(from_num, Series) else from_num
    to_val = to_num[0] if isinstance(to_num, Series) else to_num
    step_val = step_num[0] if isinstance(step_num, Series) else step_num if step_num is not None else None

    # Determine direction based on from_val and to_val
    direction = 1 if from_val <= to_val else -1

    # Use default step if none provided
    if step_val is None:
        step_val = direction

    # Prevent infinite loops
    if step_val == 0:
        raise ValueError("Step cannot be zero in pine_range")

    # Ensure step direction matches the from->to direction
    if (direction > 0 > step_val) or (direction < 0 < step_val):
        step_val = -step_val

    # Generate values
    current = from_val
    if direction > 0:
        # Ascending loop
        while current <= to_val:
            yield current
            current += step_val
            # Safety check to prevent infinite loops due to floating point precision
            if step_val > 0 and current > to_val + abs(step_val):
                break
    else:
        # Descending loop
        while current >= to_val:
            yield current
            current += step_val
            # Safety check to prevent infinite loops due to floating point precision
            if step_val < 0 and current < to_val - abs(step_val):
                break
