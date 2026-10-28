"""Проверка соответствия."""

import time
from collections import Counter, defaultdict


def exact_accept(sequence, net, initial, final, seconds=0.15):
    """Никаких добавленных токенов. Таймаут/лимит — unknown, а не отклонение."""
    from pm4py.objects.petri_net import semantics

    deadline = time.monotonic() + seconds
    hidden = [t for t in net.transitions if t.label is None]
    visible = defaultdict(list)
    for t in net.transitions:
        if t.label is not None:
            visible[t.label].append(t)
    cache = {}

    def closure(marking):
        if marking in cache:
            return cache[marking]
        found = {marking}
        todo = [marking]
        while todo:
            if time.monotonic() > deadline or len(found) > 1000:
                raise TimeoutError
            state = todo.pop()
            for t in hidden:
                if semantics.is_enabled(t, net, state):
                    following = semantics.execute(t, net, state)
                    if following not in found:
                        found.add(following)
                        todo.append(following)
        cache[marking] = found
        return found

    try:
        states = closure(initial)
        for a in sequence:
            if time.monotonic() > deadline:
                raise TimeoutError
            following = set()
            for m in states:
                for t in visible[a]:
                    if semantics.is_enabled(t, net, m):
                        following.update(closure(semantics.execute(t, net, m)))
            states = following
            if not states:
                return False
        return final in states
    except TimeoutError:
        return None
